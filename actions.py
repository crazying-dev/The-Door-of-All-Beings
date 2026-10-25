"""动作注册表与具体动作实现。

每个动作 handler 签名统一为::

    handler(req: protocol.Request, session: Session, ctx: Context) -> bytes

返回值就是直接写回客户端的帧（``OK`` / ``FAIL`` 单行）。
新增动作只需在下面加一个 ``@action("名字")``，不用改 `server.py`。

错误码统一用 `protocol.py` 的 ``E_*``，客户端凭 code 分支，message 只给人看。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

import config
import device_id as devid
import game
import protocol
import users as users_mod
from protocol import (
    E_BAD_DEVICE_ID,
    E_BAD_PAYLOAD,
    E_BAD_USER_ID,
    E_INTERNAL,
    E_NOT_AUTHENTICATED,
    E_NOT_READY,
    E_UNKNOWN_ACTION,
)
from session import Session

log = logging.getLogger(__name__)


@dataclass
class Context:
    """动作层需要的一整堆依赖（启动时注入一次）。"""

    cfg: config.Config
    store: object
    users: users_mod.user
    hub: object


_ctx: Context | None = None
ACTIONS: dict[str, Callable] = {}


def init(cfg: config.Config, store, users_handler: users_mod.user, hub) -> None:
    """注入依赖（由 main.py 在启动时调用）。"""
    global _ctx
    _ctx = Context(cfg=cfg, store=store, users=users_handler, hub=hub)


def action(name: str):
    """动作注册装饰器。"""

    def deco(fn: Callable):
        ACTIONS[name] = fn
        return fn

    return deco


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def ok(name: str, payload=None) -> bytes:
    return protocol.encode_ok(name, payload)


def fail(name: str, code: str, message: str | None = None) -> bytes:
    return protocol.encode_fail(name, code, message)


def _require_auth(req: protocol.Request, session: Session) -> bytes | None:
    """已认证返回 None，否则返回要回写的 FAIL 帧。"""
    if session.authenticated:
        return None
    return fail(req.action, E_NOT_AUTHENTICATED, "请先登录（action=login）")


def _require_ready(req: protocol.Request) -> bytes | None:
    if _ctx is None:
        return fail(req.action, E_NOT_READY)
    return None


def _version_block() -> dict:
    return {
        "server_version": config.SERVICE_VERSION,
        "protocol_version": config.PROTOCOL_VERSION,
    }


# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------

def dispatch(req: protocol.Request, session: Session) -> bytes:
    """执行一个已解析的请求，返回要回写的帧。"""
    ready = _require_ready(req)
    if ready is not None:
        return ready

    try:
        bad = _check_identity(req, session)
    except Exception:  # noqa: BLE001 - 存储层抖动不能把连接搞挂
        log.exception("校验报文身份失败 action=%s", req.action)
        return fail(req.action, E_INTERNAL)
    if bad is not None:
        return bad

    handler = ACTIONS.get(req.action)
    if handler is None:
        return fail(req.action, E_UNKNOWN_ACTION, f"Unknown action {req.action!r}")

    try:
        return handler(req, session, _ctx)
    except Exception:  # noqa: BLE001 - 单个请求不能把连接/服务搞挂
        log.exception("action %s 执行失败", req.action)
        return fail(req.action, E_INTERNAL)


def _check_identity(req: protocol.Request, session: Session) -> bytes | None:
    """校验报文携带的 device_id / user_id；不合法时返回 FAIL 帧。

    * 行③的 device_id 一旦携带，就必须能通过 HMAC 校验，并记住在本会话上；
    * 行③的 user_id 必须“确实绑定在该设备上”，且与已登录账号一致。
    """
    if req.has_device:
        if not req.device_id:
            return fail(req.action, E_BAD_DEVICE_ID, "Device ID 不是合法 hex")
        try:
            valid = devid.DeviceId_can_use(
                req.device_id, max_age_seconds=_ctx.cfg.device_id_max_age
            )
        except devid.DeviceIdError as exc:
            log.error("设备 ID 校验不可用：%s", exc)
            return fail(req.action, E_NOT_READY, "服务端设备密钥未配置")
        if not valid:
            return fail(req.action, E_BAD_DEVICE_ID)
        session.device_id_hex = req.device_id_hex

    if req.user_id:
        if not req.has_device:
            return fail(req.action, E_BAD_USER_ID, "带 user_id 时必须同时带 device_id")
        account = _ctx.users.account_by_device(req.device_id_hex)
        if account is None or account["user_id"] != req.user_id:
            return fail(req.action, E_BAD_USER_ID)
        if session.authenticated and session.user_id != req.user_id:
            return fail(req.action, E_BAD_USER_ID, "与本连接已登录账号不一致")

    return None


# ---------------------------------------------------------------------------
# 基础动作
# ---------------------------------------------------------------------------

@action("ping")
def handle_ping(req, session, ctx) -> bytes:
    """连通性/心跳测试。"""
    return ok("ping", {
        "pong_at": int(time.time()),
        "authenticated": session.authenticated,
        "user_id": session.user_id,
        **_version_block(),
    })


@action("hello")
def handle_hello(req, session, ctx) -> bytes:
    """握手：告诉客户端服务端版本、推送频率、可用动作。"""
    session.touch()
    return ok("hello", {
        "magic": protocol.MAGIC,
        **_version_block(),
        "push_hz": ctx.cfg.push_hz,
        "autosave_interval": ctx.cfg.autosave_interval,
        "device_id_length": devid.DEFAULT_LENGTH,
        "user_name_max_len": ctx.cfg.user_name_max_len,
        "actions": sorted(ACTIONS.keys()),
        "now": int(time.time()),
    })


# ---------------------------------------------------------------------------
# 设备
# ---------------------------------------------------------------------------

@action("device")
def handle_device(req, session, ctx) -> bytes:
    """设备握手：签发新设备 ID，或凭已有设备 ID 换回绑定账号。

    payload: ``<fp_hex>&<length>``（两段都可省略）。

    * 行③**带了**合法 device_id ⇒ 「确认识别」：不重新签发，
      额外返回 ``account``（该设备已绑定的账号，没绑则为 ``null``）。
      客户端开机时靠这一步实现「本机已登录过就直接进游戏」。
    * 行③为空 ⇒ 签发一张新的设备 ID，客户端必须原样保存。

    ``fp_hex`` 是客户端硬件指纹（已 sha256 过的 hex），服务端再 hash 一次
    后只存前 16 字节；不传则用随机指纹（ID 不与硬件绑定）。
    """
    parts = [p.strip() for p in req.action_data.split("&")] if req.action_data else []
    parts += [""] * (2 - len(parts))
    fp_hex, length_raw = parts[0], parts[1]

    fingerprint = b""
    if fp_hex:
        if len(fp_hex) > 256:
            return fail("device", E_BAD_PAYLOAD, "指纹过长")
        try:
            fingerprint = bytes.fromhex(fp_hex)
        except ValueError:
            return fail("device", E_BAD_PAYLOAD, "指纹不是合法 hex")

    if req.device_id:
        # 确认既有设备：ID 的 HMAC 已在 dispatch 里验过，这里只回读
        raw = req.device_id
        age = devid.DeviceId_age(raw)
        issued_at = int(time.time()) - age if age is not None else None
    else:
        length = devid.normalize_length(length_raw)
        try:
            raw = devid.DeviceId_get(length, fingerprint)
        except devid.DeviceIdError:
            log.error("签发设备 ID 失败：DEVICE_ID_SECRET 未配置")
            return fail("device", E_NOT_READY, "服务端设备密钥未配置")
        issued_at = int(time.time())

    device_id_hex = raw.hex()
    try:
        account = ctx.users.touch_device(
            device_id_hex,
            client_version=req.client_version,
            platform=req.platform,
            ip=session.ip,
        )
    except Exception:  # noqa: BLE001
        log.exception("登记设备失败")
        return fail("device", E_INTERNAL)

    return ok("device", {
        "device_id": device_id_hex,
        "length": len(raw),
        "issued_at": issued_at,
        "known": bool(req.device_id),
        "account": _account_view(account),
        **_version_block(),
    })


def _account_view(account: dict | None) -> dict | None:
    if not account:
        return None
    return {"user_id": account["user_id"], "user_name": account["user_name"]}


# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------

@action("register")
def handle_register(req, session, ctx) -> bytes:
    """注册账号。payload 只有 ``<user_name>``，user_id 由服务端生成。"""
    if not req.has_device:
        return fail("register", E_BAD_DEVICE_ID, "注册必须带合法设备 ID")
    user_name = req.action_data.strip()

    ok_flag, code, info = ctx.users.register(
        user_name,
        req.device_id_hex,
        client_version=req.client_version,
        platform=req.platform,
        ip=session.ip,
    )
    if not ok_flag:
        message = info.get("message") or protocol.ERROR_MESSAGES.get(code, code)
        if code == protocol.E_DEVICE_ALREADY_BOUND:
            # FAIL 帧装不下 account，引导客户端再调一次 device 读到绑定账号
            message = "该设备已绑定账号，请调用 device 取回账号"
        return protocol.encode_fail("register", code, message)

    session.bind_account(info["user_id"], info["user_name"])
    game.enter_world(info["user_id"], info["user_name"], session.device_id_hex)
    ctx.hub.push_event("join", info["user_id"], info["user_name"])
    log.info("%s 注册成功 -> %s", session.describe(), info["user_id"])
    return ok("register", {
        "user_id": info["user_id"],
        "user_name": info["user_name"],
        **_version_block(),
    })


@action("login")
def handle_login(req, session, ctx) -> bytes:
    """登录。payload: ``<user_id>``（留空则取行②的 user_id）。"""
    if not req.has_device:
        return fail("login", E_BAD_DEVICE_ID, "登录必须带合法设备 ID")
    user_id = req.action_data.strip() or req.user_id or session.user_id
    if not user_id:
        return fail("login", E_BAD_USER_ID, "缺少 user_id")

    ok_flag, code, info = ctx.users.login(user_id, req.device_id_hex)
    if not ok_flag:
        message = info.get("message") or protocol.ERROR_MESSAGES.get(code, code)
        return protocol.encode_fail("login", code, message)

    if session.authenticated and session.user_id != info["user_id"]:
        ctx.hub.push_event("leave", session.user_id, session.user_name)
        if not ctx.hub.has_user(session.user_id):
            game.mark_offline(session.user_id)

    session.bind_account(info["user_id"], info["user_name"])
    game.enter_world(info["user_id"], info["user_name"], session.device_id_hex)
    ctx.hub.push_event("join", info["user_id"], info["user_name"])
    log.info("%s 登录成功", session.describe())
    return ok("login", {
        "user_id": info["user_id"],
        "user_name": info["user_name"],
        "push_hz": ctx.cfg.push_hz,
        **_version_block(),
    })


@action("logout")
def handle_logout(req, session, ctx) -> bytes:
    """退出登录（连接不断，可以再登录）。"""
    if session.authenticated:
        user_id, user_name = session.user_id, session.user_name
        session.unbind_account()
        ctx.hub.push_event("leave", user_id, user_name)
        if not ctx.hub.has_user(user_id):
            game.mark_offline(user_id)
        log.info("session#%s 退出登录 %s", session.sid, user_id)
    return ok("logout", {"authenticated": False})


@action("whoami")
def handle_whoami(req, session, ctx) -> bytes:
    """当前会话的账号信息。"""
    return ok("whoami", {
        "authenticated": session.authenticated,
        "user_id": session.user_id or None,
        "user_name": session.user_name or None,
        "device_id_len": len(session.device_id_hex) // 2,
        "client_version": session.client_version or None,
        "session_id": session.sid,
        **_version_block(),
    })


# ---------------------------------------------------------------------------
# 世界 / 玩家
# ---------------------------------------------------------------------------

@action("state")
def handle_state(req, session, ctx) -> bytes:
    """上报自身位置/状态。payload: ``<x>&<y>&<z>&<yaw>&[hp]&[state]``。"""
    need = _require_auth(req, session)
    if need is not None:
        return need

    parts = [p.strip() for p in req.action_data.split("&")] if req.action_data else []
    if len(parts) < 4:
        return fail("state", E_BAD_PAYLOAD, "expect <x>&<y>&<z>&<yaw>[&hp][&state]")
    parts += [""] * (6 - len(parts))

    row = game.set_player_state(
        session.user_id,
        session.user_name,
        session.device_id_hex,
        parts[0], parts[1], parts[2], parts[3],
        hp=parts[4] or None,
        state=parts[5] or "idle",
    )
    if row is None:
        return fail("state", E_BAD_PAYLOAD, "坐标非法")
    session.touch()
    return ok("state", {"t": int(row["updated_at"]), "seq": game.revisions()[1]})


@action("peers")
def handle_peers(req, session, ctx) -> bytes:
    """拉一次在线玩家快照（与 PUSH world 同结构）。"""
    need = _require_auth(req, session)
    if need is not None:
        return need
    world_rev, players_rev = game.revisions()
    return ok("peers", {
        "players": game.players_payload(),
        "players_rev": players_rev,
        "world_rev": world_rev,
        "t": int(time.time()),
    })


@action("world")
def handle_world(req, session, ctx) -> bytes:
    """拉一次世界物品快照。"""
    world_rev, _ = game.revisions()
    items = game.world_payload()
    session.items_rev = world_rev   # 下一次 PUSH 不用再重复下发
    return ok("world", {
        "items": items,
        "world_rev": world_rev,
        "t": int(time.time()),
    })


@action("item")
def handle_item(req, session, ctx) -> bytes:
    """增/改一个世界物品（门/箱子/杆…）。

    payload: ``<id>&<pos_x>&<pos_y>&<pos_z>&<yaw>&[state]``
    或 ``<id>&<kind>&<pos_x>&<pos_y>&<pos_z>&<yaw>&[state]``（7 段时带 kind）。
    """
    need = _require_auth(req, session)
    if need is not None:
        return need

    parts = [p.strip() for p in req.action_data.split("&")] if req.action_data else []
    if len(parts) == 7:
        item_id, kind, x, y, z, yaw, state = parts
    elif len(parts) == 6:
        item_id, x, y, z, yaw, state = parts
        kind = "generic"
    else:
        return fail("item", E_BAD_PAYLOAD,
                    "expect <id>&[kind]&<x>&<y>&<z>&<yaw>&[state]")

    row = game.upsert_item({
        "id": item_id, "kind": kind,
        "pos_x": x, "pos_y": y, "pos_z": z,
        "yaw": yaw, "state": state or "idle",
    })
    if row is None:
        return fail("item", E_BAD_PAYLOAD, "物品字段非法")
    return ok("item", {"id": row["id"], "world_rev": game.revisions()[0]})


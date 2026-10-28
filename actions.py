"""动作注册表与具体动作实现。

每个动作 handler 签名统一为::

    handler(req: protocol.Request, session: Session, ctx: Context) -> bytes

返回值就是直接写回客户端的帧（``OK`` / ``FAIL`` 单行）。
新增动作只需在下面加一个 ``@action("名字")``，不用改 `server.py`。

错误码统一用 `protocol.py` 的 ``E_*``，客户端凭 code 分支，message 只给人看。
"""

from __future__ import annotations

import json
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
    """校验报文携带的 session_token / device_id / user_id。

    v1.2（带了 token 且与本会话持有的一致）：token 已经标识了这条连接，
    device_id / user_id 只在“携带了”时做形式校验，不再重复查库；
    v1.1（没带 token）：沿用旧规则，user_id 必须“确实绑在该设备上”。
    """
    bad = _validate_device(req, session)
    if bad is not None:
        return bad

    if req.has_token and req.session_token == session.session_token:
        if req.user_id and session.authenticated and session.user_id != req.user_id:
            return fail(req.action, E_BAD_USER_ID, "与本连接已登录账号不一致")
        return None

    if req.user_id:
        if not req.has_device:
            return fail(req.action, E_BAD_USER_ID, "带 user_id 时必须同时带 device_id")
        account = _ctx.users.account_by_device(req.device_id_hex)
        if account is None or account["user_id"] != req.user_id:
            return fail(req.action, E_BAD_USER_ID)
        if session.authenticated and session.user_id != req.user_id:
            return fail(req.action, E_BAD_USER_ID, "与本连接已登录账号不一致")

    return None


def _validate_device(req: protocol.Request, session: Session) -> bytes | None:
    """行③只要带了 device_id，就必须能通过 HMAC 校验。"""
    if not req.has_device:
        return None
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

    * 行③**带了**合法 device_id ⇒ 「确认识别」：不重新签发，也不写库，
      额外返回 ``account``（该设备已绑定的账号，没绑则为 ``null``）。
      客户端开机时靠这一步实现「本机已登录过就直接进游」。
    * 行③为空 ⇒ 签发一张新的设备 ID，客户端必须原样保存。
      **签发不落库**（flows.md 流程 1）：新机阶段还没有账号，
      等 register 时才把 device_id 写进 users 表。

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
        known = True
    else:
        length = devid.normalize_length(length_raw)
        try:
            raw = devid.DeviceId_get(length, fingerprint)
        except devid.DeviceIdError:
            log.error("签发设备 ID 失败：DEVICE_ID_SECRET 未配置")
            return fail("device", E_NOT_READY, "服务端设备密钥未配置")
        issued_at = int(time.time())
        known = False

    device_id_hex = raw.hex()
    session.device_id_hex = device_id_hex

    account: dict | None = None
    if known:
        # 只读：设备行已在之前的 register 里入库，这里不再 touch_device
        try:
            account = ctx.users.account_by_device(device_id_hex)
        except Exception:  # noqa: BLE001
            log.exception("读取设备绑定账号失败")
            return fail("device", E_INTERNAL)

    return ok("device", {
        "device_id": device_id_hex,
        "length": len(raw),
        "issued_at": issued_at,
        "known": known,
        "stored": known,          # 是否已在库里（新签发为 false）
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

@action("check_name")
def handle_check_name(req, session, ctx) -> bytes:
    """名称查重（注册前调，flows.md 流程 1 步骤 5）。

    payload: ``<user_name>``。返回 ``available``；不可用时用 ``reason``
    区分是重名（``taken``）还是格式非法（``invalid``）。
    """
    name = req.action_data.strip()
    available = ctx.users.check_name(name)
    reason = "" if available else (
        "taken" if users_mod.validate_user_name(name, ctx.cfg.user_name_max_len)[0]
        else "invalid"
    )
    return ok("check_name", {
        "name": name,
        "available": available,
        "reason": reason,
    })


@action("register")
def handle_register(req, session, ctx) -> bytes:
    """注册账号。payload: ``<user_name>&<race>&<player_id>``。

    ``player_id`` 是客户端生成的 UUID（本地标识），可省略；
    ``user_id`` 仍由服务端生成并作权威主键。设备 ID 到这一步才入库。
    """
    if not req.has_device:
        return fail("register", E_BAD_DEVICE_ID, "注册必须带合法设备 ID")
    parts = [p.strip() for p in req.action_data.split("&")] if req.action_data else []
    user_name = parts[0] if parts else ""
    race = parts[1] if len(parts) > 1 else ""
    player_id = parts[2] if len(parts) > 2 else ""

    ok_flag, code, info = ctx.users.register(
        user_name,
        req.device_id_hex,
        player_id,
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
    player = game.enter_world(
        info["user_id"], info["user_name"], session.device_id_hex,
        race=race, player_name=info["user_name"], player_id=info["player_id"],
    )
    ctx.hub.push_event("join", info["user_id"], info["user_name"])
    log.info("%s 注册成功 -> %s", session.describe(), info["user_id"])
    return ok("register", {
        "user_id": info["user_id"],
        "user_name": info["user_name"],
        "player_id": info["player_id"],
        "player_name": (player or {}).get("player_name", info["user_name"]),
        "race": (player or {}).get("race", game.DEFAULT_RACE),
        **_version_block(),
    })


@action("login")
def handle_login(req, session, ctx) -> bytes:
    """登录。payload: JSON ``{"player_id":"<user_id>","file_hash":"<hex>"}``。

    ``player_id`` 在登录语境下就是服务端的 ``user_id``（flows.md §8）；
    ``file_hash`` 可省（v1.1 客户端 / 旧 payload 不参与文件校验）。
    兼容旧写法：payload 不是 JSON 时整段当 user_id。
    """
    if not req.has_device:
        return fail("login", E_BAD_DEVICE_ID, "登录必须带合法设备 ID")

    payload = req.action_data.strip()
    user_id = ""
    file_hash: str | None = None
    if payload.startswith("{"):
        try:
            data = json.loads(payload)
        except ValueError:
            return fail("login", E_BAD_PAYLOAD, "login payload 不是合法 JSON")
        if not isinstance(data, dict):
            return fail("login", E_BAD_PAYLOAD, "login payload 必须是对象")
        user_id = str(data.get("player_id") or data.get("user_id") or "").strip()
        if "file_hash" in data:
            file_hash = str(data.get("file_hash") or "").strip()
    else:
        user_id = payload

    user_id = user_id or req.user_id or session.user_id
    if not user_id:
        return fail("login", E_BAD_USER_ID, "缺少 player_id / user_id")

    ok_flag, code, info = ctx.users.login(user_id, req.device_id_hex, file_hash)
    if not ok_flag:
        message = info.get("message") or protocol.ERROR_MESSAGES.get(code, code)
        return protocol.encode_fail("login", code, message)

    if session.authenticated and session.user_id != info["user_id"]:
        ctx.hub.push_event("leave", session.user_id, session.user_name)
        if not ctx.hub.has_user(session.user_id):
            game.mark_offline(session.user_id)

    # 宽限期内重连：同设备的挂起会话不能再留给扫描线程销毁（否则它会在
    # 登录后把刚刚重建的 player 行又删掉）
    taken = ctx.hub.resume(req.device_id_hex)
    if taken is not None:
        log.info(
            "session#%s 在宽限期内重连，接管旧 session#%s", session.sid, taken.sid
        )

    session.bind_account(info["user_id"], info["user_name"])
    player = game.enter_world(
        info["user_id"], info["user_name"], session.device_id_hex,
        player_name=info["user_name"], file_hash=file_hash or "",
    )
    ctx.hub.push_event("join", info["user_id"], info["user_name"])
    log.info("%s 登录成功", session.describe())
    return ok("login", {
        "user_id": info["user_id"],
        "user_name": info["user_name"],
        "player_id": (player or {}).get("player_id") or info["user_id"],
        "player_name": (player or {}).get("player_name", info["user_name"]),
        "race": (player or {}).get("race", game.DEFAULT_RACE),
        "push_hz": ctx.cfg.push_hz,
        "session_token": session.session_token,
        **_version_block(),
    })


@action("logout")
def handle_logout(req, session, ctx) -> bytes:
    """正常下线（flows.md 流程 3，动作顺序严格）。

    1. 停止本会话的 10Hz 推送（解绑账号 ⇒ 不再是已认证会话）
    2. player.online = False
    3. player 内存状态 flush 进库
    4. 销毁 player 热缓存行（重连时从库重载）
    5. 作废 session_token（拿旧 token 再来的请求会被拒）
    6. 向同世界其他玩家广播 leave
    7. 回 OK，等客户端 close（服务端不抢关，避免 OK 帧被丢）
    """
    if not session.authenticated:
        session.session_token = ""
        return ok("logout", {"authenticated": False, "saved": False})

    user_id, user_name = session.user_id, session.user_name

    # 1. 停推送
    session.unbind_account()

    # 2. 在线标记转 False（rev +1，下一步才能把这一变化带进库）
    game.mark_offline(user_id)

    # 3. 落库（必须在销毁内存行之前）
    saved = game.save_player(user_id, ctx.store)

    # 4. 销毁 player 内存
    game.destroy_player(user_id)

    # 5. 销毁 token
    session.session_token = ""

    # 6. 广播离开
    ctx.hub.push_event("leave", user_id, user_name)

    log.info("session#%s 退出登录 %s（落库=%s）", session.sid, user_id, saved)
    return ok("logout", {"authenticated": False, "saved": bool(saved), "user_id": user_id})


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
    """上报自身位置/状态。

    payload：``<x>&<y>&<z>&<yaw>[&hp][&state][&stamina][&mana]``

    后四段都可省略（客户端不必每次都带三围）。
    ``state`` 在中间是为了与 v1.1 已有报文完全兼容。
    """
    need = _require_auth(req, session)
    if need is not None:
        return need

    parts = [p.strip() for p in req.action_data.split("&")] if req.action_data else []
    if len(parts) < 4:
        return fail(
            "state", E_BAD_PAYLOAD,
            "expect <x>&<y>&<z>&<yaw>[&hp][&state][&stamina][&mana]",
        )
    parts += [""] * (8 - len(parts))

    row = game.set_player_state(
        session.user_id,
        session.user_name,
        session.device_id_hex,
        parts[0], parts[1], parts[2], parts[3],
        hp=parts[4] or None,
        state=parts[5] or "idle",
        stamina=parts[6] or None,
        mana=parts[7] or None,
    )
    if row is None:
        return fail("state", E_BAD_PAYLOAD, "坐标非法")
    session.touch()
    return ok("state", {
        "t": int(row["updated_at"]),
        "seq": game.revisions()[1],
        "rev": int(row.get("rev", 0)),
    })


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
    """拉一次世界快照（元信息 + 全部实体）。"""
    world_rev, _ = game.revisions()
    items = game.world_payload()
    session.items_rev = world_rev   # 下一次 PUSH 不用再重复下发
    return ok("world", {
        "world": game.world_meta_payload(),
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


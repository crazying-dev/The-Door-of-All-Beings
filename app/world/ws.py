"""世界通道：WebSocket 长连接 + 服务端主动广播。

端点：``/ws/world``

**握手**（连接后客户端发的第一帧）::

    {"type": "hello", "device_id": "...", "user_id": "ZWME-2WKQ",
     "user_name": "某位旅人", "client_version": "0.1.0", "state": "lobby"}

**上行**::

    {"type": "player.update", "pos": [x, y, z], "yaw": 0.0,
     "action": "walk", "state": "in_game"}
    {"type": "ping"}

**下行**::

    {"type": "welcome", "you": "ZWME-2WKQ", "tick_hz": 10, ...}
    {"type": "world", "seq": 13, "ts": 1.7e9, "players": [...], "world": []}
    {"type": "pong", "ts": 1.7e9}
    {"type": "error", "code": "BAD_FIELD", "msg": "..."}

字段不合法只回 error，不断线；只有身份校验失败 / 连接数满 / 同账号重复登录才会断开。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import current_app, request
from flask_sock import Sock
from simple_websocket import ConnectionClosed

from ..config import Config
from ..errors import ApiError
from .hub import Connection, get_hub
from .models import (
    FieldError,
    normalize_action,
    normalize_pos,
    normalize_state,
    normalize_text,
    normalize_yaw,
)
from .state import WorldState, get_state

sock = Sock()

_STOP = threading.Event()
_THREAD: threading.Thread | None = None

MAX_TEXT = 128
KICK_REASON = 4000


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
def init_websockets(app) -> None:
    """在应用工厂里调用，把 ws 蓝图挂上去。"""

    sock.init_app(app)


# --------------------------------------------------------------------------- #
# 身份校验（复用 /api/v1/verify 的规则）
# --------------------------------------------------------------------------- #
def _check_account(device_id: str, user_id: str, user_name: str) -> dict:
    from .. import db

    with db.get_cursor() as cur:
        cur.execute(
            "SELECT user_id, device_id, user_name, status, locked_until"
            " FROM accounts WHERE user_id = %s",
            (user_id,),
        )
        account = cur.fetchone()
    if account is None:
        raise ApiError("USER_NOT_FOUND", "账号不存在，请重新注册")
    if account["status"] != "active":
        raise ApiError("ACCOUNT_LOCKED", "账号已被停用")
    locked_until = account["locked_until"]
    if locked_until is not None and locked_until > datetime.now(timezone.utc):
        raise ApiError("ACCOUNT_LOCKED", "账号暂时被锁定，请稍后再试")
    if account["device_id"] != device_id:
        raise ApiError("DEVICE_MISMATCH", "账号与当前设备不匹配（账号仅能在首次绑定的设备上使用）")
    if account["user_name"] != user_name:
        raise ApiError("NAME_MISMATCH", "用户名称与账号不匹配")
    return {"user_id": account["user_id"], "user_name": account["user_name"]}


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _send_raw(ws, payload: dict) -> bool:
    try:
        from .hub import dumps

        ws.send(dumps(payload))
        return True
    except Exception:  # noqa: BLE001
        return False


def _error(ws, code: str, msg: str) -> None:
    _send_raw(ws, {"type": "error", "code": code, "msg": msg})


def _decode(raw) -> dict | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return msg if isinstance(msg, dict) else None


def _validate_update(msg: dict) -> None:
    """先校验后限流：非法字段一定要回 error，不能因为撞上限流就静默丢掉。"""

    if msg.get("pos") is not None:
        normalize_pos(msg["pos"])
    if msg.get("yaw") is not None:
        normalize_yaw(msg["yaw"])
    if msg.get("action") is not None:
        normalize_action(msg["action"])
    if msg.get("state") is not None:
        normalize_state(msg["state"])


# --------------------------------------------------------------------------- #
# 握手
# --------------------------------------------------------------------------- #
def _handshake(ws, world: WorldState, hub, peer: str) -> Connection | None:
    raw = ws.receive(timeout=Config.WS_HANDSHAKE_TIMEOUT)
    if raw is None:
        return None
    msg = _decode(raw)
    if msg is None or msg.get("type") != "hello":
        _error(ws, "BAD_REQUEST", "首帧必须是 {\"type\": \"hello\", ...} 的 JSON 对象")
        return None

    try:
        client_version = normalize_text(msg.get("client_version", ""), MAX_TEXT, "client_version")
        want_state = normalize_state(msg.get("state"))
    except FieldError as exc:
        _error(ws, "BAD_FIELD", str(exc))
        return None

    if Config.WORLD_REQUIRE_AUTH:
        try:
            device_id = normalize_text(msg.get("device_id"), MAX_TEXT, "device_id")
            user_id = normalize_text(msg.get("user_id"), MAX_TEXT, "user_id")
            user_name = normalize_text(msg.get("user_name"), MAX_TEXT, "user_name")
        except FieldError as exc:
            _error(ws, "BAD_FIELD", str(exc))
            return None
        if not device_id or not user_id or not user_name:
            _error(ws, "BAD_REQUEST", "hello 需要 device_id / user_id / user_name 三个字段")
            return None
        try:
            account = _check_account(device_id, user_id, user_name)
        except ApiError as exc:
            _error(ws, exc.code, exc.msg)
            return None
        except Exception:  # noqa: BLE001 - 数据库不可用
            current_app.logger.exception("WebSocket 握手校验失败")
            _error(ws, "SERVICE_UNAVAILABLE", "数据库不可用，暂时无法校验登录态")
            return None
        user_id = account["user_id"]
        user_name = account["user_name"]
    else:
        # 开发模式：不做设备锁校验（WORLD_REQUIRE_AUTH=0）
        try:
            user_id = normalize_text(msg.get("user_id") or "", MAX_TEXT, "user_id")
            user_name = normalize_text(msg.get("user_name") or "", MAX_TEXT, "user_name")
        except FieldError as exc:
            _error(ws, "BAD_FIELD", str(exc))
            return None
        if not user_id:
            user_id = f"dev-{uuid.uuid4().hex[:8]}"
        if not user_name:
            user_name = "访客"

    # 同一账号重复登录：踢掉旧连接（一个账号同时只保留一个在线会话）
    previous = hub.previous(user_id)
    if previous is not None:
        previous.send({"type": "error", "code": "KICKED", "msg": "同一账号在别处建立了新连接"})
        try:
            previous.ws.close(reason=KICK_REASON, message="kicked")
        except Exception:  # noqa: BLE001
            pass
        hub.unregister(previous)

    conn = hub.register(ws, user_id, peer)
    if conn is None:
        _error(ws, "SERVICE_UNAVAILABLE", "服务器连接数已满，请稍后重试")
        return None

    player = world.join(user_id, user_name, client_version, want_state)
    conn.send(
        {
            "type": "welcome",
            "you": user_id,
            "user_name": player.user_name,
            "state": player.state,
            "tick_hz": world.tick_hz,
            "server_version": Config.SERVICE_VERSION,
            "seq": world.snapshot(bump=False)["seq"],
        }
    )
    conn.send({"type": "world", **world.snapshot()})
    return conn


# --------------------------------------------------------------------------- #
# 主循环
# --------------------------------------------------------------------------- #
def _serve(ws, conn: Connection, world: WorldState) -> None:
    last_update = 0.0
    while True:
        try:
            raw = ws.receive(timeout=Config.WS_IDLE_TIMEOUT)
        except ConnectionClosed:
            return
        if raw is None:
            # 空闲超时：发一个 ping 探活（send 失败说明对端已死）
            if not conn.send({"type": "ping", "ts": round(time.time(), 3)}):
                return
            continue

        msg = _decode(raw)
        if msg is None:
            conn.send({"type": "error", "code": "BAD_REQUEST", "msg": "消息必须是 JSON 对象"})
            continue

        kind = msg.get("type")

        if kind == "ping":
            conn.send({"type": "pong", "ts": round(time.time(), 3)})
            continue

        if kind == "player.update":
            try:
                _validate_update(msg)
            except FieldError as exc:
                conn.send({"type": "error", "code": "BAD_FIELD", "msg": str(exc)})
                continue

            monotonic = time.monotonic()
            if monotonic - last_update < Config.WS_MIN_UPDATE_INTERVAL:
                continue  # 限流：超频上报直接丢弃
            last_update = monotonic
            try:
                updated = world.update_player(
                    conn.user_id,
                    pos=msg.get("pos"),
                    yaw=msg.get("yaw"),
                    action=msg.get("action"),
                    state=msg.get("state"),
                )
            except FieldError as exc:
                conn.send({"type": "error", "code": "BAD_FIELD", "msg": str(exc)})
                continue
            if updated is None:
                conn.send({"type": "error", "code": "NOT_JOINED", "msg": "玩家不在世界列表里，请重新连接"})
            continue

        if kind == "hello":
            # 重复 hello：只刷新玩家状态
            try:
                world.update_player(conn.user_id, state=msg.get("state"))
            except FieldError as exc:
                conn.send({"type": "error", "code": "BAD_FIELD", "msg": str(exc)})
            continue

        conn.send({"type": "error", "code": "BAD_REQUEST", "msg": f"未知消息类型 {kind!r}"})


@sock.route("/ws/world")
def world_socket(ws):  # pragma: no cover - 运行期长连接
    world = get_state()
    hub = get_hub()
    peer = request.remote_addr or ""
    conn: Connection | None = None
    try:
        conn = _handshake(ws, world, hub, peer)
        if conn is None:
            return
        _serve(ws, conn, world)
    except ConnectionClosed:
        pass
    except Exception:  # noqa: BLE001
        current_app.logger.exception("WebSocket 连接异常")
    finally:
        if conn is not None:
            hub.unregister(conn)
            # 同一账号可能还持有别的连接（重连瞬间），没有才从世界列表移除
            if hub.count_for(conn.user_id) == 0:
                world.leave(conn.user_id)


# --------------------------------------------------------------------------- #
# 广播线程
# --------------------------------------------------------------------------- #
def start_broadcaster(app) -> threading.Thread:
    """启动广播线程（服务端主动推位置）。幂等。"""

    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return _THREAD
    _STOP.clear()
    _THREAD = threading.Thread(target=_broadcast_loop, args=(app,), name="world-broadcast", daemon=True)
    _THREAD.start()
    return _THREAD


def stop_broadcaster() -> None:
    _STOP.set()


def _broadcast_loop(app) -> None:
    world = get_state()
    hub = get_hub()
    tick = 1.0 / max(1, world.tick_hz)
    heartbeat = max(tick, float(Config.WORLD_HEARTBEAT_SEC))
    flush_interval = max(1.0, float(Config.WORLD_FLUSH_SEC))
    last_beat = 0.0
    last_flush = time.monotonic()
    with app.app_context():
        while not _STOP.wait(tick):
            now = time.monotonic()
            if hub.count() == 0:
                continue
            world.prune(hub.user_ids())
            dirty = world.consume_dirty()
            beat = (now - last_beat) >= heartbeat
            if dirty or beat:
                last_beat = now
                hub.broadcast({"type": "world", **world.snapshot(bump=True)})
            if Config.WORLD_PERSIST and (now - last_flush) >= flush_interval:
                last_flush = now
                if world.consume_world_dirty():
                    from . import persist

                    persist.flush(world)

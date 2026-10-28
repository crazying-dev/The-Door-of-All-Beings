"""长连接会话与世界快照推送（v1.2 含会话 Token + 宽限期）。

一个 TCP 连接 = 一个 ``Session``：

* 建连时服务端**立即签发并推送 session_token**（32 位 hex），客户端后续请求携带此 token；
* 刚建连时是**匿名**的，只能调 ``ping`` / ``hello`` / ``device`` / ``register`` / ``login``；
* ``register`` / ``login`` 成功后绑定账号，变成**已认证会话**，开始收 ``PUSH``；
* 连接断了/超时，已认证会话进入 **5 秒宽限期**（suspended），期间可恢复；超时则落库销毁。

推送节奏：单线程按 ``PUSH_HZ``（默认 10Hz）遍历所有已认证会话。
"""

from __future__ import annotations

import itertools
import logging
import secrets
import socket
import threading
import time

import game
import protocol

log = logging.getLogger(__name__)

_ids = itertools.count(1)

## Token 有效期（秒）
TOKEN_EXPIRE = 180


class Session:
    """一条已建立的连接及其认证状态。"""

    __slots__ = (
        "sock", "addr", "sid", "user_id", "user_name", "device_id_hex",
        "client_version", "platform", "locale", "connected_at", "last_seen",
        "items_rev", "_lock", "_closed", "hub",
        "session_token", "token_issued_at", "suspended", "suspend_deadline",
    )

    def __init__(self, sock: socket.socket, addr, hub: "SessionHub") -> None:
        self.sock = sock
        self.addr = addr
        self.sid = next(_ids)
        self.hub = hub

        self.user_id = ""
        self.user_name = ""
        self.device_id_hex = ""
        self.client_version = ""
        self.platform = ""
        self.locale = ""

        self.connected_at = time.time()
        self.last_seen = self.connected_at
        ## 已经推给本会话的 world 修订号
        self.items_rev = -1

        ## 会话 Token（v1.2）
        self.session_token: str = ""
        self.token_issued_at: float = 0.0

        ## 宽限期状态
        self.suspended: bool = False
        self.suspend_deadline: float = 0.0

        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------ #
    # Token
    # ------------------------------------------------------------------ #

    def issue_token(self) -> str:
        """签发新的会话 Token（16 字节随机 → 32 位 hex），返回 token 值。"""
        token = secrets.token_hex(16)
        self.session_token = token
        self.token_issued_at = time.time()
        return token

    @property
    def token_valid(self) -> bool:
        """Token 是否仍然有效（非空且连接未关闭）。"""
        return self.session_token != "" and not self._closed

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #

    @property
    def authenticated(self) -> bool:
        return self.user_id != ""

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def ip(self) -> str:
        try:
            return str(self.addr[0])
        except (IndexError, TypeError):
            return ""

    def touch(self) -> None:
        self.last_seen = time.time()

    def bind_account(self, user_id: str, user_name: str) -> None:
        self.user_id = user_id
        self.user_name = user_name
        self.items_rev = -1   # 新会话补一份完整世界快照

    def unbind_account(self) -> None:
        self.user_id = ""
        self.user_name = ""
        self.items_rev = -1

    def describe(self) -> str:
        who = f"{self.user_name}({self.user_id})" if self.authenticated else "anonymous"
        return f"session#{self.sid} {self.ip} {who} v{self.client_version or '?'}"

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #

    def send(self, frame: bytes) -> bool:
        """发送一帧；失败时标记已关闭并返回 False（不抛异常）。"""
        if self._closed:
            return False
        with self._lock:
            try:
                self.sock.sendall(frame)
                return True
            except OSError as exc:
                log.info("session#%s 发送失败：%s", self.sid, exc)
                self._closed = True
                return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass


class SessionHub:
    """会话注册表 + 世界快照广播线程 + 宽限期管理。"""

    def __init__(
        self,
        push_hz: int = 10,
        max_connections: int = 256,
        grace_seconds: int = 5,
    ) -> None:
        self.push_hz = max(1, int(push_hz))
        self.max_connections = max(1, int(max_connections))
        self.grace_seconds = max(1, int(grace_seconds))
        self._lock = threading.RLock()
        self._sessions: dict[int, Session] = {}
        ## 掉线中的会话：key = session_token
        self._suspended: dict[str, Session] = {}
        self._push_stop = threading.Event()
        self._push_thread: threading.Thread | None = None
        self._grace_stop = threading.Event()
        self._grace_thread: threading.Thread | None = None
        self._seq = 0
        self._dropped = 0

        ## 宽限期过期回调（由 main.py 注入）：fn(session: Session)
        self.on_grace_expired = None

    # ------------------------------------------------------------------ #
    # 注册 / 注销
    # ------------------------------------------------------------------ #

    def try_add(self, session: Session) -> bool:
        """登记会话；超过最大连接数返回 False（调用方负责关连接）。"""
        with self._lock:
            if len(self._sessions) >= self.max_connections:
                self._dropped += 1
                return False
            self._sessions[session.sid] = session
            return True

    def add(self, session: Session) -> bool:
        return self.try_add(session)

    def remove(self, session: Session) -> None:
        with self._lock:
            self._sessions.pop(session.sid, None)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def sessions(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    def authenticated_sessions(self) -> list[Session]:
        with self._lock:
            return [s for s in self._sessions.values() if s.authenticated]

    def has_user(self, user_id: str) -> bool:
        with self._lock:
            return any(
                s.user_id == user_id and not s.closed for s in self._sessions.values()
            )

    def user_count(self) -> int:
        with self._lock:
            return len({s.user_id for s in self._sessions.values() if s.authenticated})

    def close_all(self) -> None:
        for session in self.sessions():
            session.close()
        with self._lock:
            self._sessions.clear()
        # 也清理所有挂起的
        with self._lock:
            self._suspended.clear()

    # ------------------------------------------------------------------ #
    # Token 推送
    # ------------------------------------------------------------------ #

    def push_token(self, session: Session) -> None:
        """向 session 推送新签发的 token（连接建立时调用）。"""
        frame = protocol.encode_push(protocol.TOPIC_TOKEN, {
            "token": session.session_token,
            "expire": TOKEN_EXPIRE,
        })
        session.send(frame)

    def push_reset_token(self, session: Session) -> None:
        """推送 reset_token 通知（宽限期后旧 token 作废时调用）。"""
        frame = protocol.encode_push(protocol.TOPIC_RESET_TOKEN, {
            "reason": "expired",
        })
        session.send(frame)

    # ------------------------------------------------------------------ #
    # 宽限期
    # ------------------------------------------------------------------ #

    def suspend(self, session: Session) -> None:
        """连接意外断开后进入宽限期（仅对已认证会话有意义）。"""
        with self._lock:
            self._sessions.pop(session.sid, None)
            session.suspended = True
            session.suspend_deadline = time.time() + self.grace_seconds
            if session.session_token:
                self._suspended[session.session_token] = session
            else:
                # 没 token 就挂不住（v1.1 旧客户端）：按设备号留位，重连仍可接管
                self._suspended[f"device:{session.device_id_hex}:{session.sid}"] = session
        log.info(
            "session#%s %s 进入宽限期（%ds）",
            session.sid,
            session.user_id or "anonymous",
            self.grace_seconds,
        )

    def resume(self, device_id_hex: str) -> Session | None:
        """宽限期内同设备重连：把挂起会话从销毁队列里摘掉并返回它。

        返回的是**旧** session（socket 已死），只用于日志；它不会被放回
        ``_sessions``（一条死连接不该再收推送），也不再被扫描线程销毁。
        新连接自己已在 ``_sessions`` 里。
        """
        if not device_id_hex:
            return None
        now = time.time()
        with self._lock:
            for token, session in list(self._suspended.items()):
                if session.device_id_hex != device_id_hex:
                    continue
                if session.suspend_deadline <= now:
                    continue          # 已经超时，让扫描线程走正常销毁
                del self._suspended[token]
                session.suspended = False
                session.session_token = ""   # 旧 token 彻底作废
                return session
        return None

    def start_grace_scanner(self) -> None:
        """启动宽限期扫描线程（每秒一次）。"""
        if self._grace_thread is not None and self._grace_thread.is_alive():
            return
        self._grace_stop.clear()
        self._grace_thread = threading.Thread(
            target=self._grace_loop, name="grace-scanner", daemon=True
        )
        self._grace_thread.start()
        log.info("宽限期扫描线程已启动（grace=%ds）", self.grace_seconds)

    def stop_grace_scanner(self) -> None:
        self._grace_stop.set()
        if self._grace_thread is not None:
            self._grace_thread.join(timeout=2.0)
            self._grace_thread = None

    def _grace_loop(self) -> None:
        while not self._grace_stop.is_set():
            try:
                self._check_expired()
            except Exception:  # noqa: BLE001
                log.exception("宽限期扫描出错")
            self._grace_stop.wait(1.0)

    def _check_expired(self) -> None:
        now = time.time()
        expired: list[Session] = []
        with self._lock:
            for token, session in list(self._suspended.items()):
                if session.suspend_deadline <= now:
                    expired.append(session)
                    del self._suspended[token]
        for session in expired:
            log.info(
                "session#%s %s 宽限期超时，执行销毁",
                session.sid, session.user_id or "?",
            )
            if self.on_grace_expired:
                try:
                    self.on_grace_expired(session)
                except Exception:  # noqa: BLE001
                    log.exception("on_grace_expired 回调失败")

    # ------------------------------------------------------------------ #
    # 广播
    # ------------------------------------------------------------------ #

    def push_event(self, kind: str, user_id: str, user_name: str = "") -> None:
        """广播一条事件帧（加入 / 离开）。"""
        frame = protocol.encode_push(
            protocol.TOPIC_EVENT,
            {"kind": kind, "user_id": user_id, "user_name": user_name,
             "t": int(time.time())},
        )
        for session in self.authenticated_sessions():
            if session.user_id == user_id:
                continue
            session.send(frame)

    def push_world_once(self) -> int:
        """推一轮世界快照；返回成功发送的会话数。"""
        players = game.players_payload()
        world_rev, players_rev = game.revisions()
        with self._lock:
            self._seq += 1
            seq = self._seq
        now = time.time()

        base = {
            "t": round(now, 3),
            "seq": seq,
            "players_rev": players_rev,
            "players": players,
            "online": len(players),
        }
        with_items: bytes | None = None
        sent = 0
        for session in self.authenticated_sessions():
            if session.closed:
                continue
            if session.items_rev != world_rev:
                if with_items is None:
                    payload = dict(base)
                    payload["items"] = game.world_payload()
                    payload["world_rev"] = world_rev
                    with_items = protocol.encode_push(protocol.TOPIC_WORLD, payload)
                session.send(with_items)
                session.items_rev = world_rev
            else:
                session.send(protocol.encode_push(protocol.TOPIC_WORLD, base))
            sent += 1
        return sent

    def start_pusher(self) -> None:
        if self._push_thread is not None and self._push_thread.is_alive():
            return
        self._push_stop.clear()
        self._push_thread = threading.Thread(
            target=self._push_loop, name="world-pusher", daemon=True
        )
        self._push_thread.start()
        log.info("世界快照推送已启动（%d Hz）", self.push_hz)

    def stop_pusher(self) -> None:
        self._push_stop.set()
        if self._push_thread is not None:
            self._push_thread.join(timeout=2.0)
            self._push_thread = None

    def _push_loop(self) -> None:
        interval = 1.0 / self.push_hz
        next_at = time.monotonic()
        while not self._push_stop.is_set():
            try:
                if self.authenticated_sessions():
                    self.push_world_once()
            except Exception:  # noqa: BLE001
                log.exception("世界快照推送出错")
            next_at += interval
            delay = next_at - time.monotonic()
            if delay < 0:
                next_at = time.monotonic()
                delay = interval
            self._push_stop.wait(delay)

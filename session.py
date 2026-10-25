"""长连接会话与世界快照推送。

一个 TCP 连接 = 一个 ``Session``：

* 刚建连时是**匿名**的，只能调 ``ping`` / ``hello`` / ``device`` / ``register`` / ``login``；
* ``register`` / ``login`` 成功后绑定账号，变成**已认证会话**，开始收 ``PUSH``；
* 连接断了/超时/被踢，会话注销，玩家标离线（若同一账号还有别的会话则保持在线）。

推送节奏：单线程按 ``PUSH_HZ``（默认 10Hz）遍历所有已认证会话。
世界快照每 tick 只构造一次（而不是每个会话构造一次），
每个会话只在 ``world_rev`` 变了之后才多带一份 ``items``。
"""

from __future__ import annotations

import itertools
import logging
import socket
import threading
import time

import game
import protocol

log = logging.getLogger(__name__)

_ids = itertools.count(1)


class Session:
    """一条已建立的连接及其认证状态。"""

    __slots__ = (
        "sock", "addr", "sid", "user_id", "user_name", "device_id_hex",
        "client_version", "platform", "locale", "connected_at", "last_seen",
        "items_rev", "_lock", "_closed", "hub",
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

        self._lock = threading.Lock()
        self._closed = False

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
    """会话注册表 + 世界快照广播线程。"""

    def __init__(self, push_hz: int = 10, max_connections: int = 256) -> None:
        self.push_hz = max(1, int(push_hz))
        self.max_connections = max(1, int(max_connections))
        self._lock = threading.RLock()
        self._sessions: dict[int, Session] = {}
        self._push_stop = threading.Event()
        self._push_thread: threading.Thread | None = None
        self._seq = 0
        self._dropped = 0

    # ------------------------------------------------------------------ #
    # 注册
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
                continue   # 自己不需要收自己的上下线事件
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
        # 绝大多数会话都不需要 world 物品：先算一次带物品的版本，懒构造
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
            except Exception:  # noqa: BLE001 - 广播线程不能死
                log.exception("世界快照推送出错")
            next_at += interval
            delay = next_at - time.monotonic()
            if delay < 0:          # 跑不动就重新对齐，不要追帧，否则越差越多
                next_at = time.monotonic()
                delay = interval
            self._push_stop.wait(delay)

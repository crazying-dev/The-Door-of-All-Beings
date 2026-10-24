"""WebSocket 连接登记与广播。

只关心「谁在线」与「把一包 JSON 发给所有人」，不关心业务字段。
"""

from __future__ import annotations

import json
import threading


def dumps(payload: dict) -> str:
    """统一的紧凑序列化：不转义中文，不浪费带宽。"""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class Connection:
    """一个 WebSocket 连接。"""

    __slots__ = ("id", "ws", "user_id", "peer", "connected_at")

    def __init__(self, cid: int, ws, user_id: str, peer: str, connected_at: float) -> None:
        self.id = cid
        self.ws = ws
        self.user_id = user_id
        self.peer = peer
        self.connected_at = connected_at

    def send(self, payload: dict) -> bool:
        """发送一包 JSON；对端已断开返回 False（不抛异常）。"""

        try:
            self.ws.send(dumps(payload))
            return True
        except Exception:  # noqa: BLE001 - 已断开 / 管道破裂都当失败处理
            return False


class Hub:
    """连接表：连接 id → Connection，以及 user_id → 连接 id 的反向索引。"""

    def __init__(self, max_clients: int = 256) -> None:
        self._lock = threading.RLock()
        self._conns: dict[int, Connection] = {}
        self._by_user: dict[str, int] = {}
        self._next_id = 1
        self.max_clients = max(1, int(max_clients))

    def register(self, ws, user_id: str, peer: str = "") -> Connection | None:
        """登记新连接；同一账号重复连接时保留最新一条（旧的由调用方关闭）。"""

        import time

        with self._lock:
            if len(self._conns) >= self.max_clients:
                return None
            cid = self._next_id
            self._next_id += 1
            conn = Connection(cid, ws, user_id, peer, time.time())
            self._conns[cid] = conn
            self._by_user[user_id] = cid
            return conn

    def previous(self, user_id: str) -> Connection | None:
        """返回该账号现有的旧连接（用于踢下线）。"""

        with self._lock:
            cid = self._by_user.get(user_id)
            return self._conns.get(cid) if cid is not None else None

    def unregister(self, conn: Connection) -> None:
        with self._lock:
            self._conns.pop(conn.id, None)
            if self._by_user.get(conn.user_id) == conn.id:
                self._by_user.pop(conn.user_id, None)

    def user_ids(self) -> set[str]:
        with self._lock:
            return set(self._by_user.keys())

    def count_for(self, user_id: str) -> int:
        with self._lock:
            return sum(1 for conn in self._conns.values() if conn.user_id == user_id)

    def count(self) -> int:
        with self._lock:
            return len(self._conns)

    def broadcast(self, payload: dict) -> int:
        """广播一包 JSON，返回成功发送的连接数。"""

        with self._lock:
            conns = list(self._conns.values())
        if not conns:
            return 0
        text = dumps(payload)
        dead: list[Connection] = []
        sent = 0
        for conn in conns:
            try:
                conn.ws.send(text)
                sent += 1
            except Exception:  # noqa: BLE001
                dead.append(conn)
        for conn in dead:
            self.unregister(conn)
        return sent


_HUB: Hub | None = None


def get_hub() -> Hub:
    global _HUB
    if _HUB is None:
        from ..config import Config

        _HUB = Hub(max_clients=Config.WORLD_MAX_CLIENTS)
    return _HUB

"""服务端权威的世界状态（内存，线程安全）。

生命周期约定：

  * ``players`` —— 纯内存。进程重启后没有任何玩家在线，历史坐标没有意义，
    因此不落库；掉线即从列表移除（离线玩家不应占据广播带宽）。
  * ``world``   —— 可选持久化（见 persist.py），重启后由数据库回灌。

所有读写都拿同一把 RLock：WebSocket 每个连接一个线程，
广播线程与 HTTP 快照接口会与它们并发。
"""

from __future__ import annotations

import threading
import time

from .models import (
    DEFAULT_STATE,
    Player,
    WorldItem,
    normalize_action,
    normalize_pos,
    normalize_state,
    normalize_yaw,
)


class WorldState:
    """一张内存表装两个列表：玩家与世界物品。"""

    def __init__(self, tick_hz: int = 10, stale_seconds: float = 60.0) -> None:
        self._lock = threading.RLock()
        self._players: dict[str, Player] = {}
        self._world: dict[str, WorldItem] = {}
        self._seq = 0
        self._dirty = True
        self._world_dirty = False
        self._last_change = 0.0
        self.tick_hz = max(1, int(tick_hz))
        self.stale_seconds = max(5.0, float(stale_seconds))

    # ------------------------------------------------------------------ #
    # 玩家列表
    # ------------------------------------------------------------------ #
    def join(self, user_id: str, user_name: str, client_version: str = "", state: str | None = None) -> Player:
        """玩家上线（同一账号重连则保留位置、刷新身份）。"""

        with self._lock:
            player = self._players.get(user_id)
            if player is None:
                player = Player(user_id=user_id, user_name=user_name, client_version=client_version or "")
                player.state = normalize_state(state if state is not None else DEFAULT_STATE)
                self._players[user_id] = player
            else:
                if user_name:
                    player.user_name = user_name
                if client_version:
                    player.client_version = client_version
                if state is not None:
                    player.state = normalize_state(state)
                player.updated_at = time.time()
            self._touch()
            return player

    def update_player(
        self,
        user_id: str,
        *,
        pos: object = None,
        yaw: object = None,
        action: object = None,
        state: object = None,
    ) -> Player | None:
        """更新玩家位置 / 面向角度 / 动作 / 状态；玩家不在列表里则返回 None。"""

        with self._lock:
            player = self._players.get(user_id)
            if player is None:
                return None
            if pos is not None:
                player.pos = normalize_pos(pos)
            if yaw is not None:
                player.yaw = normalize_yaw(yaw)
            if action is not None:
                player.action = normalize_action(action)
            if state is not None:
                player.state = normalize_state(state)
            player.updated_at = time.time()
            self._touch()
            return player

    def leave(self, user_id: str) -> bool:
        with self._lock:
            if self._players.pop(user_id, None) is None:
                return False
            self._touch()
            return True

    def prune(self, connected: set[str]) -> int:
        """清掉已经没有连接的玩家（半开连接的兵底）。"""

        with self._lock:
            gone = [uid for uid in self._players if uid not in connected]
            for uid in gone:
                del self._players[uid]
            if gone:
                self._touch()
            return len(gone)

    def player(self, user_id: str) -> Player | None:
        with self._lock:
            return self._players.get(user_id)

    def player_count(self) -> int:
        with self._lock:
            return len(self._players)

    # ------------------------------------------------------------------ #
    # 世界物品列表（现在为空）
    # ------------------------------------------------------------------ #
    def world_items(self) -> list[dict]:
        with self._lock:
            return [item.to_dict() for item in self._world.values()]

    def upsert_world_item(
        self,
        item_id: str,
        *,
        kind: str | None = None,
        pos: object = None,
        yaw: object = None,
        state: str | None = None,
    ) -> dict:
        """新增 / 更新一个可移动物品的位置。

        现在还没有调用方（世界物品列表先留空），但接口与表结构已就绪，
        以后客户端上报物品位移时直接用这个入口。
        """

        with self._lock:
            key = str(item_id)
            item = self._world.get(key)
            if item is None:
                item = WorldItem(id=key)
                self._world[key] = item
            if kind:
                item.kind = str(kind)
            if pos is not None:
                item.pos = normalize_pos(pos)
            if yaw is not None:
                item.yaw = normalize_yaw(yaw)
            if state:
                item.state = str(state)
            item.updated_at = time.time()
            self._world_dirty = True
            self._touch()
            return item.to_dict()

    def remove_world_item(self, item_id: str) -> bool:
        with self._lock:
            if self._world.pop(str(item_id), None) is None:
                return False
            self._world_dirty = True
            self._touch()
            return True

    def world_count(self) -> int:
        with self._lock:
            return len(self._world)

    # ------------------------------------------------------------------ #
    # 快照
    # ------------------------------------------------------------------ #
    def snapshot(self, bump: bool = True) -> dict:
        """生成下发快照：``{seq, ts, players, world}``。"""

        with self._lock:
            if bump:
                self._seq += 1
            return {
                "seq": self._seq,
                "ts": round(time.time(), 3),
                "players": [player.to_dict() for player in self._players.values()],
                "world": [item.to_dict() for item in self._world.values()],
            }

    def consume_dirty(self) -> bool:
        with self._lock:
            if not self._dirty:
                return False
            self._dirty = False
            return True

    def consume_world_dirty(self) -> bool:
        with self._lock:
            if not self._world_dirty:
                return False
            self._world_dirty = False
            return True

    def counts(self) -> dict:
        with self._lock:
            return {"players": len(self._players), "world": len(self._world)}

    def _touch(self) -> None:
        self._dirty = True
        self._last_change = time.time()


_STATE: WorldState | None = None


def get_state() -> WorldState:
    """全局单例（首次调用时按 Config 初始化）。"""

    global _STATE
    if _STATE is None:
        from ..config import Config

        _STATE = WorldState(tick_hz=Config.WORLD_TICK_HZ, stale_seconds=Config.WORLD_STALE_SEC)
    return _STATE

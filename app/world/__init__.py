"""世界状态：玩家列表与世界物品列表（服务端权威，WebSocket 实时下发）。

模块划分：

    models.py   实体与字段校验
    state.py    线程安全的世界状态（内存）
    hub.py      WebSocket 连接登记与广播
    ws.py       /ws/world 端点与广播线程
    persist.py  世界物品位置的 PostgreSQL 持久化（可选）
"""

from .models import (
    DEFAULT_ACTION,
    DEFAULT_STATE,
    PLAYER_ACTIONS,
    PLAYER_STATES,
    FieldError,
    Player,
    PlayerState,
    WorldItem,
)
from .state import WorldState, get_state

__all__ = [
    "DEFAULT_ACTION",
    "DEFAULT_STATE",
    "PLAYER_ACTIONS",
    "PLAYER_STATES",
    "FieldError",
    "Player",
    "PlayerState",
    "WorldItem",
    "WorldState",
    "get_state",
]

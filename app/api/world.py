"""世界状态的只读 HTTP 接口（WebSocket 的降级方案 / 调试 / 探活）。

实时同步走 ``/ws/world``；这个端点只用于：
  * 客户端拿不到 WebSocket 时用轮询降级；
  * 运维/调试直接看一眼服务端当前认为的世界长什么样。
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..world.state import get_state

bp = Blueprint("world", __name__, url_prefix="/api/v1/world")


@bp.get("/state")
def world_state():
    """两个列表的全量快照：``players`` + ``world``。"""

    snapshot = get_state().snapshot(bump=False)
    return jsonify(ok=True, **snapshot)


@bp.get("/players")
def world_players():
    """只要玩家列表。"""

    snapshot = get_state().snapshot(bump=False)
    return jsonify(ok=True, seq=snapshot["seq"], ts=snapshot["ts"], players=snapshot["players"])


@bp.get("/items")
def world_items():
    """只要世界物品列表（现在为空）。"""

    snapshot = get_state().snapshot(bump=False)
    return jsonify(ok=True, seq=snapshot["seq"], ts=snapshot["ts"], world=snapshot["world"])

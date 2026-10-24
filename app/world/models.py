"""世界状态的实体定义与字段校验。

服务端对外只暴露两个列表：

  * ``players`` —— 玩家列表：身份、玩家状态、位置、面向角度、动作
  * ``world``   —— 世界中可移动物品的列表：位置、朝向、状态（当前为空表）

所有进入状态的数据都要经过本模块的 ``normalize_*`` 函数校验，
避免 NaN / 无穷大 / 超范围坐标污染广播快照。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlayerState(str, Enum):
    """玩家状态。

    ``offline`` 只作为语义占位：在线连接的玩家一定处于 ``lobby`` 或 ``in_game``，
    断线后玩家直接从 ``players`` 列表移除（进程重启同理），所以快照里不会出现它。
    """

    OFFLINE = "offline"
    LOBBY = "lobby"
    IN_GAME = "in_game"


PLAYER_STATES: tuple[str, ...] = tuple(item.value for item in PlayerState)

# 动作名必须与 Godot 侧 player_rig.gd 的 AnimationPlayer 动画名一致，
# 客户端只负责上报，服务端只做白名单校验。
PLAYER_ACTIONS: tuple[str, ...] = ("idle", "walk", "run", "jump", "fall")

DEFAULT_ACTION = "idle"
DEFAULT_STATE = PlayerState.LOBBY.value

# 坐标绝对值上限：超过即认为客户端数据异常（世界远小于这个量级）
MAX_COORD = 100000.0
FULL_TURN = 360.0


class FieldError(ValueError):
    """字段非法（会被 WebSocket 层转成 error 消息，而不是断线）。"""


# --------------------------------------------------------------------------- #
# 字段校验
# --------------------------------------------------------------------------- #
def normalize_pos(value: Any) -> list[float]:
    """把 ``[x, y, z]`` 归一化成 4 位小数的有限浮点列表。"""

    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise FieldError("pos 必须是长度为 3 的数组 [x, y, z]")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise FieldError("pos 的元素必须是数字")
        number = float(item)
        if not math.isfinite(number):
            raise FieldError("pos 的元素必须是有限数值")
        if abs(number) > MAX_COORD:
            raise FieldError(f"pos 超出允许范围（|{number:g}| > {MAX_COORD:g}）")
        result.append(round(number, 4))
    return result


def normalize_yaw(value: Any) -> float:
    """面向角度：角度制，统一折算到 ``[0, 360)``。

    与客户端 hud.gd 的约定保持一致——0° 指向世界 -Z（正北），顺时针增大。
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FieldError("yaw 必须是数字（角度制）")
    number = float(value)
    if not math.isfinite(number):
        raise FieldError("yaw 必须是有限数值")
    return round(number % FULL_TURN, 4)


def normalize_action(value: Any) -> str:
    if value is None:
        return DEFAULT_ACTION
    if not isinstance(value, str):
        raise FieldError("action 必须是字符串")
    text = value.strip().lower()
    if text not in PLAYER_ACTIONS:
        raise FieldError("action 非法，可选：" + ", ".join(PLAYER_ACTIONS))
    return text


def normalize_state(value: Any) -> str:
    if value is None:
        return DEFAULT_STATE
    if not isinstance(value, str):
        raise FieldError("state 必须是字符串")
    text = value.strip().lower()
    if text not in PLAYER_STATES:
        raise FieldError("state 非法，可选：" + ", ".join(PLAYER_STATES))
    return text


def normalize_text(value: Any, limit: int, label: str) -> str:
    if not isinstance(value, str):
        raise FieldError(f"{label} 必须是字符串")
    text = value.strip()
    if len(text) > limit:
        raise FieldError(f"{label} 过长（上限 {limit} 字符）")
    return text


# --------------------------------------------------------------------------- #
# 实体
# --------------------------------------------------------------------------- #
@dataclass
class Player:
    """玩家列表中的一项。"""

    user_id: str
    user_name: str
    state: str = DEFAULT_STATE
    pos: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    yaw: float = 0.0
    action: str = DEFAULT_ACTION
    client_version: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "state": self.state,
            "pos": list(self.pos),
            "yaw": self.yaw,
            "action": self.action,
            "client_version": self.client_version,
            "updated_at": round(self.updated_at, 3),
        }


@dataclass
class WorldItem:
    """世界中可移动物品列表中的一项（现在还没有任何物品，结构先定型）。"""

    id: str
    kind: str = "generic"
    pos: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    yaw: float = 0.0
    state: str = "idle"
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.kind,
            "pos": list(self.pos),
            "yaw": self.yaw,
            "state": self.state,
            "updated_at": round(self.updated_at, 3),
        }

"""运行时内存权威：世界物品 + 在线玩家，定时落库。

设计思路（沿原 `my/game.py`，把两个坑修了）：

* 内存是**权威**，数据库只是快照；每 ``AUTOSAVE_INTERVAL`` 秒 upsert 一次；
  落库失败不丢数据，写 ``./tmp/databaseErrordb.json`` 兼底。
* 读写全走一把 ``RLock``；但 ``snapshot()`` 返回的是**拷贝**，持锁时间极短。
  （旧版 ``snapshot()`` 直接返回锁对象，游说者完全不知道要做什么，
  而且遍历时拿着锁、遇到阻塞 IO 会把全服一起卡住。）
* 每个集合带一个修订号 ``rev``：推送时只带变化的部分，10Hz 全量广播也不肉疼。

===========================================================================
world 元素结构（对应表 world_items）
---------------------------------------------------------------------------
{
  "id":     "door_01",  # 全局唯一标识（主键）
  "kind":   "door",     # door 门 / crate 箱子 / lever 杆 / generic 通用…
  "pos_x":  12.5,       # 世界坐标 X（米，右为正）
  "pos_y":  0.0,        # 世界坐标 Y（米，上为正，地面 = 0）
  "pos_z":  -8.0,       # 世界坐标 Z（米）
  "yaw":    90.0,       # 绕 Y 轴朝向（度，0 = 正北 / -Z）
  "state":  "idle",     # idle / moving / open / locked
}

players 元素结构（对应表 players）
---------------------------------------------------------------------------
{
  "user_id":   "U10001",  # 账号 ID（主键）
  "user_name": "勇者",
  "device_id": "ab cd…",  # 绑定的设备 ID hex
  "pos_x":     12.5,      # 最后已知位置
  "pos_y":     0.0,
  "pos_z":     -8.0,
  "yaw":       180.0,     # 镜头水平朝向（度）
  "hp":        100.0,
  "state":     "moving",  # idle / moving / dead
  "online":    True,
  "updated_at": 1761379200.0,
}
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 运行时内存数据
# ---------------------------------------------------------------------------
world: dict[str, dict] = {}
players: dict[str, dict] = {}

_lock = threading.RLock()
_world_rev = 0
_players_rev = 0

## 坐标合法性上限（米）：超出直接丢弃，避免 NaN / inf 污染内存与数据库
COORD_LIMIT = 100000.0
## 允许的玩家状态
PLAYER_STATES = ("idle", "moving", "dead")


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def _norm_yaw(yaw) -> float:
    """把任意角度归一到 [0, 360)；非法值回 0。"""
    try:
        value = float(yaw)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return value % 360.0


def _coord(value) -> float | None:
    """归一坐标；非法/越界返回 None。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or abs(out) > COORD_LIMIT:
        return None
    return out


@contextmanager
def snapshot() -> Iterator[tuple[list[dict], list[dict]]]:
    """只读快照：持锁期间拷贝，出来就释放锁。"""
    yield _snapshot_lists()


def _snapshot_lists() -> tuple[list[dict], list[dict]]:
    """拷贝一份当前内存数据（持锁极短）。"""
    with _lock:
        items = [dict(it) for it in world.values()]
        ps = [dict(p) for p in players.values()]
    return items, ps


@contextmanager
def mutate():
    """写操作上下文：增删改 ``world`` / ``players`` 必须持有。"""
    with _lock:
        yield


def revisions() -> tuple[int, int]:
    """(world_rev, players_rev)。"""
    with _lock:
        return _world_rev, _players_rev


def bump_world_rev() -> int:
    global _world_rev
    with _lock:
        _world_rev += 1
        return _world_rev


def bump_players_rev() -> int:
    global _players_rev
    with _lock:
        _players_rev += 1
        return _players_rev


# ---------------------------------------------------------------------------
# 世界物品
# ---------------------------------------------------------------------------

def upsert_item(item: dict) -> dict | None:
    """新增/更新一个世界物品；字段非法返回 None。"""
    item_id = str(item.get("id", "")).strip()
    if not item_id or "|" in item_id:
        return None
    pos = [_coord(item.get(k, 0.0)) for k in ("pos_x", "pos_y", "pos_z")]
    if any(v is None for v in pos):
        return None
    row = {
        "id": item_id,
        "kind": str(item.get("kind", "generic")).strip() or "generic",
        "pos_x": pos[0],
        "pos_y": pos[1],
        "pos_z": pos[2],
        "yaw": _norm_yaw(item.get("yaw", 0.0)),
        "state": str(item.get("state", "idle")).strip() or "idle",
        "updated_at": time.time(),
    }
    with mutate():
        world[item_id] = row
    bump_world_rev()
    return row


def world_payload() -> list[dict]:
    with _lock:
        return [
            {
                "id": it["id"],
                "kind": it["kind"],
                "pos_x": it["pos_x"],
                "pos_y": it["pos_y"],
                "pos_z": it["pos_z"],
                "yaw": it["yaw"],
                "state": it["state"],
            }
            for it in world.values()
        ]


# ---------------------------------------------------------------------------
# 玩家
# ---------------------------------------------------------------------------

def set_player_state(
    user_id: str,
    user_name: str,
    device_id_hex: str,
    pos_x,
    pos_y,
    pos_z,
    yaw,
    hp=None,
    state: str = "idle",
) -> dict | None:
    """写入一名玩家的最新状态（坐标非法返回 None）。"""
    if not user_id:
        return None
    pos = [_coord(pos_x), _coord(pos_y), _coord(pos_z)]
    if any(v is None for v in pos):
        return None
    hp_value = _coord(hp) if hp not in (None, "") else 100.0
    if hp_value is None:
        hp_value = 100.0
    row = {
        "user_id": user_id,
        "user_name": user_name,
        "device_id": device_id_hex,
        "pos_x": pos[0],
        "pos_y": pos[1],
        "pos_z": pos[2],
        "yaw": _norm_yaw(yaw),
        "hp": max(0.0, min(100.0, hp_value)),
        "state": state if state in PLAYER_STATES else "idle",
        "online": True,
        "updated_at": time.time(),
    }
    with mutate():
        players[user_id] = row
    bump_players_rev()
    return row


def remove_player(user_id: str) -> bool:
    """下线一个玩家（保留历史数据的话改为只置 online=False）。"""
    with mutate():
        existed = players.pop(user_id, None) is not None
    if existed:
        bump_players_rev()
    return existed


def enter_world(
    user_id: str,
    user_name: str = "",
    device_id_hex: str = "",
) -> dict | None:
    """玩家进入世界（注册/登录成功后调）。

    没有行就在原点建一个；已有行（比如上次登录的位置）则保留坐标、
    只把 ``online`` 置回 True。这样重启后玩家还会出现在上次站的地方。
    """
    if not user_id:
        return None
    with mutate():
        row = players.get(user_id)
        if row is None:
            row = {
                "user_id": user_id,
                "user_name": user_name,
                "device_id": device_id_hex,
                "pos_x": 0.0,
                "pos_y": 0.0,
                "pos_z": 0.0,
                "yaw": 0.0,
                "hp": 100.0,
                "state": "idle",
                "online": True,
                "updated_at": time.time(),
            }
            players[user_id] = row
        else:
            row["user_name"] = user_name or row.get("user_name", "")
            row["device_id"] = device_id_hex or row.get("device_id", "")
            row["online"] = True
            row["updated_at"] = time.time()
        result = dict(row)
    bump_players_rev()
    return result


def mark_offline(user_id: str) -> None:
    with mutate():
        row = players.get(user_id)
        if row is None:
            return
        row["online"] = False
        row["state"] = "idle"
        row["updated_at"] = time.time()
    bump_players_rev()


def players_payload(include_offline: bool = False) -> list[dict]:
    with _lock:
        return [
            {
                "user_id": p["user_id"],
                "user_name": p.get("user_name", ""),
                "pos_x": p["pos_x"],
                "pos_y": p["pos_y"],
                "pos_z": p["pos_z"],
                "yaw": p["yaw"],
                "hp": p.get("hp", 100.0),
                "state": p.get("state", "idle"),
            }
            for p in players.values()
            if include_offline or p.get("online", False)
        ]


def online_count() -> int:
    with _lock:
        return sum(1 for p in players.values() if p.get("online", False))


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------

_dump_path = Path(__file__).resolve().parent / "tmp" / "databaseErrordb.json"


def save(store=None) -> bool:
    """把内存快照 upsert 到存储；失败时写本地 JSON 兼底。"""
    backend = store or _store
    if backend is None:
        log.warning("落库跳过：存储层未初始化")
        return False
    items, ps = _snapshot_lists()
    # 在 players 表里 device_id 是外键，空的不能拿去 INSERT（会报 FK 违例）
    ps = [p for p in ps if p.get("user_id") and p.get("device_id")]
    try:
        backend.save_snapshot(items, ps)
        return True
    except Exception as exc:  # noqa: BLE001 - 落库失败不能拖垮整个服务
        log.error("落库失败，写本地快照：%s", exc)
        _dump_snapshot(items, ps)
        return False


def _dump_snapshot(items: list[dict] | None = None, ps: list[dict] | None = None) -> None:
    """数据库不可用时的兼底：全量快照写 ./tmp/databaseErrordb.json。"""
    try:
        if items is None or ps is None:
            with snapshot() as (snap_items, snap_ps):
                items, ps = snap_items, snap_ps
        _dump_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "world": items,
            "players": ps,
        }
        _dump_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        log.info("快照已写入 %s", _dump_path.resolve())
    except Exception as exc:  # noqa: BLE001
        log.error("快照写入也失败了：%s", exc)


# ---------------------------------------------------------------------------
# 启动 / 定时落库
# ---------------------------------------------------------------------------
_store = None
_timer: threading.Timer | None = None
_interval = 60


def load_from_store(store) -> None:
    """启动时把 disc 里的世界物品读回内存；玩家表不预载（在线状态由会话决定）。"""
    try:
        rows = store.load_world() or []
    except Exception as exc:  # noqa: BLE001
        log.error("读取世界数据失败：%s", exc)
        return
    with mutate():
        for row in rows:
            item_id = str(row.get("id", "")).strip()
            if item_id:
                world[item_id] = dict(row)
    bump_world_rev()
    log.info("已从存储载入 %d 个世界物品", len(rows))


def start_autosave(store, interval: int = 60) -> None:
    """启动定时落库循环（daemon 线程，进程退出自动结束）。"""
    global _store, _interval
    _store = store
    _interval = max(5, int(interval))
    try:
        store.mark_all_offline()
    except Exception as exc:  # noqa: BLE001
        log.warning("重置在线标记失败：%s", exc)
    _schedule_next()
    log.info("定时落库已启动（每 %d 秒）", _interval)


def stop_autosave() -> None:
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None


def _schedule_next() -> None:
    global _timer
    _timer = threading.Timer(_interval, _tick)
    _timer.daemon = True
    _timer.start()


def _tick() -> None:
    try:
        save()
    except Exception:  # noqa: BLE001 - 定时线程不能死
        log.exception("定时落库异常")
    finally:
        _schedule_next()  # 无论成败都排下一轮，避免一次失败终止落库

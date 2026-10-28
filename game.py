"""运行时热缓存：世界实体 + 玩家状态。

**权威在数据库，不在内存。** 这是与旧版最大的区别：

| 旧版 | 现在 |
|---|---|
| 内存权威，每 60s 把内存写进库 | 库权威，内存只是缓存；启动从库读回 |
| 写库直接覆盖 | 写库带 ``rev`` 门禁，落后数据直接被库拒掉 |
| 落库失败只写本地 JSON | 同样写本地 JSON 兜底，但下次启动以库为准 |

因此“重启会丢数据”不再是真的：重启只会丢掉**未落库的最后几秒**，
而那些数据本来也不能覆盖库里更新的快照。

===========================================================================
world 元素结构（对应表 world_item）
---------------------------------------------------------------------------
{
  "id": "door_01",      # 全局唯一标识（主键）
  "world_id": "default", # 所属世界
  "kind": "door",        # door / crate / lever / generic …
  "pos_x": 12.5,
  "pos_y": 0.0,
  "pos_z": -8.0,
  "yaw": 90.0,            # 度，0 = 正北 / -Z
  "state": "idle",
  "rev": 3,               # 修订号：只有比库里大才能覆盖
  "updated_at": 1761379200.0,
}

players 元素结构（对应表 player）
---------------------------------------------------------------------------
{
  "user_id": "ZWME-2WKQ", # 主键（= users.user_id）
  "user_name": "勇者",     # 账号名（来自 users 表，只读）
  "player_name": "勇者",   # 角色名（游戏内显示）
  "race": "human",         # 种族
  "device_id": "ab cd…",  # 绑定设备（只在内存里，不落库）
  "pos_x": 12.5, "pos_y": 0.0, "pos_z": -8.0, "yaw": 180.0,
  "state": "moving",       # idle / moving / dead
  "online": True,
  "hp_max": 100.0, "hp": 88.0,
  "stamina_max": 100.0, "stamina": 100.0,
  "mana_max": 100.0, "mana": 100.0,
  "rev": 7,
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
# 运行时热缓存
# ---------------------------------------------------------------------------
world: dict[str, dict] = {}        # item_id -> world_item 行
players: dict[str, dict] = {}      # user_id -> player 行
world_meta: dict[str, dict] = {}   # world_id -> world 行

_lock = threading.RLock()
## 推送用的全局修订号（单调递增，不等于行级 rev）
_world_rev = 0
_players_rev = 0

## 坐标合法性上限（米）：超出直接丢弃，避免 NaN / inf 污染内存与数据库
COORD_LIMIT = 100000.0
## 允许的玩家状态
PLAYER_STATES = ("idle", "moving", "dead")

DEFAULT_WORLD = "default"
DEFAULT_RACE = "human"
## 三围默认值（上限与当前值都从 100 起）
DEFAULT_STATS = {
    "hp_max": 100.0,
    "hp": 100.0,
    "stamina_max": 100.0,
    "stamina": 100.0,
    "mana_max": 100.0,
    "mana": 100.0,
}


# ---------------------------------------------------------------------------
# 归一工具
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


def _num(value, default: float = 0.0) -> float:
    """宽容取数：非数字 / NaN / inf 都回默认值。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


def _apply_stat(row: dict, key: str, value) -> None:
    """写一项当前值（hp / stamina / mana），自动夹到 [0, 上限]。

    ``None`` / 空串 = “这次没上报”，保持原值不动（客户端不必每次都带三围）。
    """
    if value is None or value == "":
        return
    raw = _coord(value)
    if raw is None:
        return
    cap = _num(row.get(f"{key}_max"), 100.0)
    row[key] = max(0.0, min(cap, raw))


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

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
    """(world_rev, players_rev)——推送用的全局序号。"""
    with _lock:
        return _world_rev, _players_rev


def _default_world_row() -> dict:
    return {
        "id": DEFAULT_WORLD,
        "name": "众生之门",
        "seed": 0,
        "rev": 0,
    }


def _touch_world_rev() -> int:
    """推世界全局修订号，同时把世界行的 rev 抬到同一高度。"""
    global _world_rev
    with _lock:
        _world_rev += 1
        meta = world_meta.setdefault(DEFAULT_WORLD, _default_world_row())
        meta["rev"] = _world_rev
        meta["updated_at"] = time.time()
        return _world_rev


def bump_world_rev() -> int:
    return _touch_world_rev()


def bump_players_rev() -> int:
    global _players_rev
    with _lock:
        _players_rev += 1
        return _players_rev


# ---------------------------------------------------------------------------
# 世界物品
# ---------------------------------------------------------------------------

def upsert_item(item: dict) -> dict | None:
    """新增/更新一个世界物品；字段非法返回 None。每次修改 rev + 1。"""
    item_id = str(item.get("id", "")).strip()
    if not item_id or "|" in item_id:
        return None
    pos = [_coord(item.get(k, 0.0)) for k in ("pos_x", "pos_y", "pos_z")]
    if any(v is None for v in pos):
        return None

    with mutate():
        prev = world.get(item_id) or {}
        row = {
            "id": item_id,
            "world_id": str(item.get("world_id") or prev.get("world_id") or DEFAULT_WORLD),
            "kind": str(item.get("kind", "generic")).strip() or "generic",
            "pos_x": pos[0],
            "pos_y": pos[1],
            "pos_z": pos[2],
            "yaw": _norm_yaw(item.get("yaw", 0.0)),
            "state": str(item.get("state", "idle")).strip() or "idle",
            "rev": int(prev.get("rev", 0)) + 1,
            "updated_at": time.time(),
        }
        world[item_id] = row
        result = dict(row)
    _touch_world_rev()
    return result


def world_payload() -> list[dict]:
    with _lock:
        return [
            {
                "id": it["id"],
                "kind": it.get("kind", "generic"),
                "pos_x": it.get("pos_x", 0.0),
                "pos_y": it.get("pos_y", 0.0),
                "pos_z": it.get("pos_z", 0.0),
                "yaw": it.get("yaw", 0.0),
                "state": it.get("state", "idle"),
            }
            for it in world.values()
        ]


def world_meta_payload() -> dict:
    """世界元信息（给 ``world`` 动作下发名称 / 种子）。"""
    with _lock:
        row = dict(world_meta.get(DEFAULT_WORLD) or _default_world_row())
    return {
        "id": row.get("id", DEFAULT_WORLD),
        "name": row.get("name", ""),
        "seed": int(row.get("seed", 0) or 0),
        "rev": int(row.get("rev", 0) or 0),
    }


# ---------------------------------------------------------------------------
# 玩家
# ---------------------------------------------------------------------------

def _new_player_row(
    user_id: str,
    user_name: str = "",
    device_id_hex: str = "",
    race: str = "",
    player_name: str = "",
    player_id: str = "",
    file_hash: str = "",
) -> dict:
    row = {
        "user_id": user_id,
        "user_name": user_name or "",
        "player_id": player_id or "",        # 客户端生成的 UUID
        "file_hash": file_hash or "",       # 登录校验文件哈希
        "player_name": player_name or user_name or "",
        "race": race or DEFAULT_RACE,
        "device_id": device_id_hex or "",
        "pos_x": 0.0,
        "pos_y": 0.0,
        "pos_z": 0.0,
        "yaw": 0.0,
        "state": "idle",
        "online": True,
        # 从当前全局修订号起算：下线后销毁了缓存行、重连重建时，
        # rev 从 0 起会被库里上一轮的旧 rev 拒掉（反而丢数据）。
        "rev": _players_rev,
        "updated_at": time.time(),
    }
    row.update(DEFAULT_STATS)
    return row


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
    stamina=None,
    mana=None,
) -> dict | None:
    """写入一名玩家的最新状态（坐标非法返回 None）。每次写入 rev + 1。"""
    if not user_id:
        return None
    pos = [_coord(pos_x), _coord(pos_y), _coord(pos_z)]
    if any(v is None for v in pos):
        return None

    with mutate():
        row = players.get(user_id)
        if row is None:
            row = _new_player_row(user_id, user_name, device_id_hex)
            players[user_id] = row
        row["pos_x"], row["pos_y"], row["pos_z"] = pos[0], pos[1], pos[2]
        row["yaw"] = _norm_yaw(yaw)
        if user_name:
            row["user_name"] = user_name
        if device_id_hex:
            row["device_id"] = device_id_hex
        row["state"] = state if state in PLAYER_STATES else "idle"
        _apply_stat(row, "hp", hp)
        _apply_stat(row, "stamina", stamina)
        _apply_stat(row, "mana", mana)
        row["online"] = True
        row["rev"] = int(row.get("rev", 0)) + 1
        row["updated_at"] = time.time()
        result = dict(row)
    bump_players_rev()
    return result


def remove_player(user_id: str) -> bool:
    """下线一个玩家（直接从缓存移除；归档数据仍在库里）。"""
    with mutate():
        existed = players.pop(user_id, None) is not None
    if existed:
        bump_players_rev()
    return existed


def _normalize_player_row(row: dict) -> dict | None:
    """把库里读回的 player 行归一成内存形状。

    启动预载与「销毁后重连重载」共用这一份规则，两边行为必须一致：
    ``online`` 一律 False（拿到这条数据的人还没上线，由 enter_world 置 True），
    ``rev`` 原样继承（否则写库会被 rev 门禁拒掉）。
    """
    user_id = str(row.get("user_id") or "").strip()
    if not user_id:
        return None
    return {
        "user_id": user_id,
        "user_name": str(row.get("user_name") or ""),
        "player_id": str(row.get("player_id") or ""),
        "file_hash": str(row.get("file_hash") or ""),
        "player_name": str(row.get("player_name") or row.get("user_name") or ""),
        "race": str(row.get("race") or DEFAULT_RACE),
        "device_id": "",          # 设备不在 player 表里
        "pos_x": _num(row.get("pos_x")),
        "pos_y": _num(row.get("pos_y")),
        "pos_z": _num(row.get("pos_z")),
        "yaw": _norm_yaw(row.get("yaw")),
        "state": str(row.get("state") or "idle"),
        "online": False,           # 重启后/重载时没人在线
        "hp_max": _num(row.get("hp_max"), 100.0),
        "hp": _num(row.get("hp"), 100.0),
        "stamina_max": _num(row.get("stamina_max"), 100.0),
        "stamina": _num(row.get("stamina"), 100.0),
        "mana_max": _num(row.get("mana_max"), 100.0),
        "mana": _num(row.get("mana"), 100.0),
        "rev": int(row.get("rev") or 0),
        "updated_at": _num(row.get("updated_at"), time.time()),
    }


def enter_world(
    user_id: str,
    user_name: str = "",
    device_id_hex: str = "",
    race: str = "",
    player_name: str = "",
    player_id: str = "",
    file_hash: str = "",
    store=None,
) -> dict | None:
    """玩家进入世界（注册/登录成功后调）。

    缓存里没有该玩家时，先从库读一次（下线时被 ``destroy_player`` 销毁过），
    读到就原位重进，读不到才新建行 —— 这就是 flows.md 流程 4 里的「player 重载」。
    坐标与三围都从库里那条最新记录恢复，不会回到出生点。

    ``player_id`` / ``file_hash`` 只在当前为空时填，不能把库里已有的值刷掉。
    """
    if not user_id:
        return None

    # DB 读取必须在锁外：否则一个慢查询会卡住 10Hz 推送线程
    backend = store or _store
    stored_row: dict | None = None
    if get_player(user_id) is None and backend is not None:
        try:
            stored_row = backend.load_player(user_id)
        except Exception as exc:  # noqa: BLE001 - 读不到就当作新玩家
            log.warning("重载玩家 %s 失败：%s", user_id, exc)
            stored_row = None

    with mutate():
        row = players.get(user_id)
        if row is None:
            if stored_row is not None:
                row = _normalize_player_row(stored_row) or _new_player_row(user_id)
            else:
                row = _new_player_row(
                    user_id, user_name, device_id_hex, race, player_name,
                    player_id, file_hash,
                )
            players[user_id] = row
        if user_name:
            row["user_name"] = user_name
        if player_name:
            row["player_name"] = player_name or row.get("player_name", "")
        if race:
            row["race"] = race
        if device_id_hex:
            row["device_id"] = device_id_hex
        if player_id and not row.get("player_id"):
            row["player_id"] = player_id
        if file_hash and not row.get("file_hash"):
            row["file_hash"] = file_hash
        row["online"] = True
        row["rev"] = int(row.get("rev", 0)) + 1
        row["updated_at"] = time.time()
        result = dict(row)
    bump_players_rev()
    return result


def mark_offline(user_id: str) -> None:
    """标为离线：**只改 online，不删 key**（彻底移除见 ``destroy_player``）。

    保留缓存行是有意的：宽限期内重连、或同账号另一条连接上线时，
    坐标与三围不应该丢。
    """
    with mutate():
        row = players.get(user_id)
        if row is None:
            return
        row["online"] = False
        row["state"] = "idle"
        row["rev"] = int(row.get("rev", 0)) + 1
        row["updated_at"] = time.time()
    bump_players_rev()


def destroy_player(user_id: str) -> bool:
    """销毁一名玩家的热缓存行（正常下线 / 宽限期超时都走这里）。

    与 ``mark_offline`` 的区别：这里是**彻底移除**，同时清掉
    “已落库 rev” 记录，下次该玩家重连时从库中重新加载。
    调用前必须已经把数据 flush 进库（``save_player``）。
    """
    global _players_rev
    with _lock:
        existed = players.pop(user_id, None) is not None
        _persisted_player_revs.pop(str(user_id), None)
        if existed:
            _players_rev += 1
    return existed


def players_payload(include_offline: bool = False) -> list[dict]:
    with _lock:
        return [
            {
                "user_id": p["user_id"],
                "user_name": p.get("user_name", ""),
                "player_name": p.get("player_name", "") or p.get("user_name", ""),
                "race": p.get("race", DEFAULT_RACE),
                "pos_x": p.get("pos_x", 0.0),
                "pos_y": p.get("pos_y", 0.0),
                "pos_z": p.get("pos_z", 0.0),
                "yaw": p.get("yaw", 0.0),
                "state": p.get("state", "idle"),
                "hp_max": p.get("hp_max", 100.0),
                "hp": p.get("hp", 100.0),
                "stamina_max": p.get("stamina_max", 100.0),
                "stamina": p.get("stamina", 100.0),
                "mana_max": p.get("mana_max", 100.0),
                "mana": p.get("mana", 100.0),
            }
            for p in players.values()
            if include_offline or p.get("online", False)
        ]


def online_count() -> int:
    with _lock:
        return sum(1 for p in players.values() if p.get("online", False))


def get_player(user_id: str) -> dict | None:
    with _lock:
        row = players.get(user_id)
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------

_dump_path = Path(__file__).resolve().parent / "tmp" / "databaseErrordb.json"
_store = None
_timer: threading.Timer | None = None
_interval = 60

## “已经落过库”的修订号：只把比它新的行交给数据库。
## 没有它的话，什么都没变也会每轮重写一遍全表（库在远端，代价很大），
## 而且库里 rev 相等的行会被当成“落后”而报一堆假警告。
_persisted_world_rev = 0
_persisted_item_revs: dict[str, int] = {}
_persisted_player_revs: dict[str, int] = {}


def save(store=None) -> bool:
    """把**有变化**的行 upsert 到数据库；落后数据会被库拒掉（只记日志，不算失败）。

    判定“有变化”用行级 ``rev``：内存 rev 比“已经落过库的 rev”大才算脏。
    这样 10Hz 上报的玩家每轮只写自己一行，而挂机的玩家一行也不写。
    """
    backend = store or _store
    if backend is None:
        log.warning("落库跳过：存储层未初始化")
        return False

    global _persisted_world_rev, _persisted_item_revs, _persisted_player_revs

    with _lock:
        all_items, all_players = _snapshot_lists_unlocked()
        meta = dict(world_meta.get(DEFAULT_WORLD) or _default_world_row())
        meta["rev"] = max(int(meta.get("rev", 0) or 0), _world_rev)
        world_row = meta if int(meta.get("rev", 0) or 0) > _persisted_world_rev else None
        items = [it for it in all_items
                 if int(it.get("rev", 0) or 0) > _persisted_item_revs.get(it["id"], -1)]
        ps = [p for p in all_players
              if p.get("user_id")
              and int(p.get("rev", 0) or 0) > _persisted_player_revs.get(p["user_id"], -1)]

    try:
        world_result = backend.save_world(world_row, items)
        player_result = backend.save_players(ps)
    except Exception as exc:  # noqa: BLE001 - 落库失败不能拖垮整个服务
        log.error("落库失败，写本地快照：%s", exc)
        _dump_snapshot(all_items, all_players)
        return False

    stale = list(world_result.get("stale") or []) + list(player_result.get("stale") or [])
    written = int(world_result.get("written") or 0) + int(player_result.get("written") or 0)
    stale_set = set(str(x) for x in stale)

    # 写入成功的行记下“已落库 rev”；被拒的行**不记**，
    # 但也不再重试（库里那个值更权威，下一轮仍然会被拒）。
    with _lock:
        if world_row and str(world_row["id"]) not in stale_set:
            _persisted_world_rev = int(world_row.get("rev", 0) or 0)
        for it in items:
            if str(it["id"]) not in stale_set:
                _persisted_item_revs[str(it["id"])] = int(it.get("rev", 0) or 0)
        for p in ps:
            if str(p["user_id"]) not in stale_set:
                _persisted_player_revs[str(p["user_id"])] = int(p.get("rev", 0) or 0)

    if stale:
        log.warning(
            "有 %d 条数据落后于数据库，已被拒绝写入（以库为准）：%s",
            len(stale), ", ".join(str(x) for x in stale[:8]),
        )
    if written or stale:
        log.info("落库：写入 %d 行，落后被拒 %d 行", written, len(stale))
    else:
        log.debug("落库：无变化，跳过")
    return True


def save_player(user_id: str, store=None) -> bool:
    """单独 flush 一名玩家（不走定时落库）。

    下线 / 宽限期超时时用：先把最后一帧写进库，再销毁内存行。
    落后于库的行会被 rev 门禁拒掉，但仍返回 True（不报错，以库为准）。
    """
    backend = store or _store
    if backend is None:
        log.warning("单行落库跳过：存储层未初始化")
        return False
    if not user_id:
        return False

    with _lock:
        row = players.get(user_id)
        payload = dict(row) if row else None

    if payload is None:
        return True      # 已经没有这一行（重复下线），没东西可写

    try:
        result = backend.save_players([payload])
    except Exception as exc:  # noqa: BLE001 - 落库失败也要留证据
        log.error("玩家 %s 落库失败，写本地快照：%s", user_id, exc)
        _dump_snapshot()
        return False

    stale = [str(x) for x in (result.get("stale") or [])]
    written = int(result.get("written") or 0)
    if user_id not in stale:
        with _lock:
            _persisted_player_revs[str(user_id)] = int(payload.get("rev", 0) or 0)
    if stale:
        log.warning("玩家 %s 的数据落后于库，本次落库被拒（以库为准）", user_id)
    elif written:
        log.info("玩家 %s 已落库（rev=%d）", user_id, int(payload.get("rev", 0) or 0))
    return True


def _snapshot_lists_unlocked() -> tuple[list[dict], list[dict]]:
    """同 ``_snapshot_lists()``，但调用方已持锁。"""
    return ([dict(it) for it in world.values()],
            [dict(p) for p in players.values()])


def _dump_snapshot(items: list[dict] | None = None, ps: list[dict] | None = None) -> None:
    """数据库不可用时的兜底：全量快照写 ./tmp/databaseErrordb.json。"""
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

def load_from_store(store) -> bool:
    """从数据库载入权威数据（启动阶段 s2）。

    * 世界元信息 + 世界实体全部读回；
    * 玩家全部读回，但 ``online`` 一律强制 False（重启后没人在线）；
    * 内存里的行级 ``rev`` 全部继承库里的值，下次写入才能正确地只接受领先数据。
    """
    global _world_rev, _players_rev
    global _persisted_world_rev
    ok = True

    try:
        world_data = store.load_world(DEFAULT_WORLD) or {}
    except Exception as exc:  # noqa: BLE001
        log.error("读取世界数据失败：%s", exc)
        world_data = {}
        ok = False

    try:
        player_rows = store.load_players() or []
    except Exception as exc:  # noqa: BLE001
        log.error("读取玩家数据失败：%s", exc)
        player_rows = []
        ok = False

    world_row = world_data.get("world") or {}
    item_rows = world_data.get("items") or []

    with mutate():
        world.clear()
        players.clear()
        world_meta.clear()

        if world_row.get("id"):
            world_meta[str(world_row["id"])] = {
                "id": str(world_row["id"]),
                "name": str(world_row.get("name") or ""),
                "seed": int(world_row.get("seed") or 0),
                "rev": int(world_row.get("rev") or 0),
            }

        for row in item_rows:
            item_id = str(row.get("id") or "").strip()
            if not item_id:
                continue
            world[item_id] = {
                "id": item_id,
                "world_id": str(row.get("world_id") or DEFAULT_WORLD),
                "kind": str(row.get("kind") or "generic"),
                "pos_x": _num(row.get("pos_x")),
                "pos_y": _num(row.get("pos_y")),
                "pos_z": _num(row.get("pos_z")),
                "yaw": _norm_yaw(row.get("yaw")),
                "state": str(row.get("state") or "idle"),
                "rev": int(row.get("rev") or 0),
                "updated_at": _num(row.get("updated_at"), time.time()),
            }

        for row in player_rows:
            new_row = _normalize_player_row(row)
            if new_row is not None:
                players[new_row["user_id"]] = new_row

        _world_rev = max(
            [int(it.get("rev") or 0) for it in world.values()]
            + [int((world_meta.get(DEFAULT_WORLD) or {}).get("rev") or 0), 0]
        )
        _players_rev = max([int(p.get("rev") or 0) for p in players.values()] + [0])

        # 刚从库里读回来的行就是“已落库”的状态，不需要再写一遍
        _persisted_world_rev = int((world_meta.get(DEFAULT_WORLD) or {}).get("rev") or 0)
        _persisted_item_revs.clear()
        _persisted_item_revs.update(
            {iid: int(it.get("rev") or 0) for iid, it in world.items()}
        )
        _persisted_player_revs.clear()
        _persisted_player_revs.update(
            {uid: int(p.get("rev") or 0) for uid, p in players.items()}
        )

    log.info(
        "已从数据库载入权威数据：世界 %s（rev=%d）/ 实体 %d 个 / 玩家 %d 条",
        (world_meta.get(DEFAULT_WORLD) or {}).get("name") or DEFAULT_WORLD,
        _world_rev, len(world), len(players),
    )
    return ok


def start_autosave(store, interval: int = 60) -> None:
    """启动定时落库循环（daemon 线程，进程退出自动结束）。

    注意：这里**不再**顺手把库里 online 清成 False。
    那件事必须在 ``load_from_store()`` **之前**做（见 main.py 的 s2），
    否则会把刚上线的玩家又刷成离线。
    """
    global _store, _interval
    _store = store
    _interval = max(5, int(interval))
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


def reset() -> None:
    """清空热缓存（测试用；不改数据库）。"""
    global _world_rev, _players_rev, _persisted_world_rev
    with mutate():
        world.clear()
        players.clear()
        world_meta.clear()
        _world_rev = 0
        _players_rev = 0
        _persisted_world_rev = 0
        _persisted_item_revs.clear()
        _persisted_player_revs.clear()

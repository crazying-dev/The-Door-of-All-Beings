"""
运行时数据库，存储世界和玩家数据
world: 存储世界数据
players: 存储玩家数据
每分钟落硬盘库一次，操作数据强要求锁
落库失败时快照写入 ./tmp/databaseErrordb.json

===========================================================================
world 元素结构（对应表 world_items）
---------------------------------------------------------------------------
{
  "id":     "door_01",  # 物品全局唯一标识（主键），命名建议 <类型>_<序号>
  "kind":   "door",     # 物品类型：door 门 / crate 箱子 / lever 杆 / generic 通用…
  "pos_x":  12.5,       # 世界坐标 X（米，右为正）
  "pos_y":  0.0,        # 世界坐标 Y（米，上为正，地面=0）
  "pos_z":  -8.0,       # 世界坐标 Z（米，屏幕内/南为正；-Z 为场景正前方）
  "yaw":    90.0,       # 绕 Y 轴朝向（度，0=正北/-Z，顺时针增大）
  "state":  "idle",     # 状态机：idle 静止 / moving 运动中 / open 已开启 / locked 已上锁
}

===========================================================================
players 元素结构（对应表 players）
---------------------------------------------------------------------------
{
  "user_id":   "U10001",   # 账号 ID（主键，关联 accounts.user_id）
  "device_id": "ab cd…",   # 绑定的设备 ID hex 串（关联 devices.device_id）
  "pos_x":     12.5,       # 最后已知世界坐标 X（米）
  "pos_y":     1.7,        # 最后已知世界坐标 Y（米，眼睛高度约 1.7）
  "pos_z":     -8.0,       # 最后已知世界坐标 Z（米）
  "yaw":       180.0,      # 镜头水平朝向（度）
  "hp":        100.0,      # 当前生命值（上限 100，<=0 时通常为 dead）
  "state":     "moving",   # 行为状态：idle 待机 / moving 移动 / dead 死亡 / offline 离线
  "online":    True,       # 本次快照是否在线（由心跳判定，无心跳则 False）
}

坐标右手系：+X 右、+Y 上、-Z 前方； yaw 以度为单位，不做归一化也可，入库前请用
 _norm_yaw() 归一到 [0, 360)。
"""

import json
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 运行时内存数据（字段含义见模块顶部详细说明）
# world:   世界可互动物品的权威位置与状态
# players: 玩家运行时档案与最后已知位置
# ---------------------------------------------------------------------------
world: list[dict] = []
players: list[dict] = []

# 操作 world / players 强要求锁：落库时 snapshot()，改动数据时 mutate()
_lock = threading.RLock()


def _norm_yaw(yaw) -> float:
    """将任意角度归一到 [0, 360)。非法值回 0。"""
    try:
        return float(yaw) % 360.0
    except (TypeError, ValueError):
        return 0.0


def snapshot():
    """只读快照上下文：遍历时防止并发修改"""
    return _lock


def mutate():
    """写操作上下文：增删改 world / players 必须持有"""
    return _lock


def save(db=None) -> bool:
    """
    保存数据到硬盘数据库：
      - world   upsert 到 world_items 表
      - players upsert 到 players 表
    如果数据库出错，open("w") 打开 ./tmp/databaseErrordb.json 保存快照。

    Parameters
    ----------
    db : db.Database 实例；为 None 时使用全局 _db（由 start_autosave 注入）
    """
    db = db or _db
    try:
        if db is None:
            raise RuntimeError("Database 未初始化")
        with snapshot():
            items = [dict(it) for it in world]        # 拷贝，缩短持锁时间
            ps = [dict(p) for p in players]
        with db.conn() as conn, conn.cursor() as cur:
            # ---- world_items ----
            for it in items:
                cur.execute(
                    """
                    INSERT INTO world_items (id, kind, pos_x, pos_y, pos_z, yaw, state, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE SET
                        kind = EXCLUDED.kind,
                        pos_x = EXCLUDED.pos_x,
                        pos_y = EXCLUDED.pos_y,
                        pos_z = EXCLUDED.pos_z,
                        yaw = EXCLUDED.yaw,
                        state = EXCLUDED.state,
                        updated_at = now()
                    """,
                    (
                        it.get("id"),
                        it.get("kind", "generic"),
                        it.get("pos_x", 0.0),
                        it.get("pos_y", 0.0),
                        it.get("pos_z", 0.0),
                        _norm_yaw(it.get("yaw", 0.0)),
                        it.get("state", "idle"),
                    ),
                )
            # ---- players ----
            for p in ps:
                cur.execute(
                    """
                    INSERT INTO players
                        (user_id, device_id, pos_x, pos_y, pos_z, yaw, hp, state, online, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (user_id) DO UPDATE SET
                        device_id = EXCLUDED.device_id,
                        pos_x = EXCLUDED.pos_x,
                        pos_y = EXCLUDED.pos_y,
                        pos_z = EXCLUDED.pos_z,
                        yaw = EXCLUDED.yaw,
                        hp = EXCLUDED.hp,
                        state = EXCLUDED.state,
                        online = EXCLUDED.online,
                        updated_at = now()
                    """,
                    (
                        p.get("user_id"),
                        p.get("device_id", ""),
                        p.get("pos_x", 0.0),
                        p.get("pos_y", 0.0),
                        p.get("pos_z", 0.0),
                        _norm_yaw(p.get("yaw", 0.0)),
                        float(p.get("hp", 100.0)),
                        p.get("state", "idle"),
                        bool(p.get("online", False)),
                    ),
                )
        return True
    except Exception as e:
        print(f"[game] 落库失败，写入本地快照: {e}")
        _dump_snapshot()
        return False


def _dump_snapshot() -> None:
    """数据库不可用时的兜底：全量快照写 ./tmp/databaseErrordb.json"""
    try:
        tmp_dir = Path("./tmp")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / "databaseErrordb.json"
        with snapshot():
            data = {
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "world": [dict(it) for it in world],
                "players": [dict(p) for p in players],
            }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[game] 快照已写入 {path.resolve()}")
    except Exception as e2:
        print(f"[game] 快照写入也失败了: {e2}")


# ---------------------------------------------------------------------------
# 定时落库
# ---------------------------------------------------------------------------
_db = None
_timer: threading.Timer | None = None
_interval = 60  # 每分钟落硬盘库一次


def start_autosave(db, interval: int = 60) -> None:
    """启动定时落库循环（daemon 线程，进程退出自动结束）。"""
    global _db, _timer, _interval
    _db = db
    _interval = interval
    _mark_all_offline(db)   # 重启后无人在新会话中上线，先将遗留行标离线
    _schedule_next()


def _mark_all_offline(db) -> None:
    """启动时将库内所有 players.online 重置为 FALSE（与真实在线状态对齐）。"""
    try:
        with db.conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE players SET online = FALSE WHERE online = TRUE")
    except Exception as e:
        print(f"[game] 重置在线标记失败: {e}")


def stop_autosave() -> None:
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None


def _schedule_next() -> None:
    global _timer
    _timer = threading.Timer(_interval, _tick, daemon=True)
    _timer.start()


def _tick() -> None:
    save()
    _schedule_next()  # 无论成败都排下一轮，避免一次失败终止落库

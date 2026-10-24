"""世界物品位置的 PostgreSQL 持久化（可选，best-effort）。

只有 ``world`` 列表落库：玩家位置是高频临时数据，进程重启后本来就无人在线，
落库只会留下一堆幽灵坐标。世界物品（门、箱子、可推动的物件）才是真正的世界状态。

任何数据库错误只告警、不影响实时同步（内存态永远为准）。
"""

from __future__ import annotations

import sys

from ..config import Config
from .state import WorldState


def enabled() -> bool:
    return bool(Config.WORLD_PERSIST and Config.DATABASE_URL)


def _warn(text: str) -> None:
    print(f"[world] {text}", file=sys.stderr, flush=True)


def load(world: WorldState) -> int:
    """启动时把 world_items 表回灌进内存；返回加载条数。"""

    if not enabled():
        return 0
    try:
        from .. import db

        with db.get_cursor() as cur:
            cur.execute(
                "SELECT id, kind, pos_x, pos_y, pos_z, yaw, state FROM world_items ORDER BY id"
            )
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        _warn(f"读取 world_items 失败：{exc!r}")
        return 0

    loaded = 0
    for row in rows:
        try:
            world.upsert_world_item(
                row["id"],
                kind=row["kind"],
                pos=[row["pos_x"], row["pos_y"], row["pos_z"]],
                yaw=row["yaw"],
                state=row["state"],
            )
            loaded += 1
        except Exception as exc:  # noqa: BLE001
            _warn(f"跳过损坏的世界物品 {row.get('id')!r}：{exc!r}")
    # 回灌不是业务变更，不触发一次无意义的落库
    world.consume_world_dirty()
    return loaded


def flush(world: WorldState) -> int:
    """把内存中的世界物品全量 upsert 回数据库；返回写入条数。"""

    if not enabled():
        return 0
    items = world.world_items()
    try:
        from .. import db

        with db.get_cursor(dict_rows=False) as cur:
            for item in items:
                pos = item["pos"]
                cur.execute(
                    "INSERT INTO world_items (id, kind, pos_x, pos_y, pos_z, yaw, state, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, now())"
                    " ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind,"
                    " pos_x = EXCLUDED.pos_x, pos_y = EXCLUDED.pos_y, pos_z = EXCLUDED.pos_z,"
                    " yaw = EXCLUDED.yaw, state = EXCLUDED.state, updated_at = now()",
                    (
                        item["id"],
                        item["type"],
                        pos[0],
                        pos[1],
                        pos[2],
                        item["yaw"],
                        item["state"],
                    ),
                )
    except Exception as exc:  # noqa: BLE001
        _warn(f"写入 world_items 失败：{exc!r}")
        return 0
    return len(items)

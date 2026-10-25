"""旧表 -> 新表迁移（devices / accounts / players / world_items -> users / player / world_item）。

用法::

    uv run scripts/migrate_legacy.py            # 迁移并改名
    uv run scripts/migrate_legacy.py --dry-run  # 只看会做什么

为什么需要它
------------
V1 的表叫 ``devices`` / ``accounts`` / ``players`` / ``world_items``；
V2 改成 ``users`` / ``player`` / ``world`` / ``world_item``（库在远端，
旧表里可能已经有真实账号）。直接不要旧数据会丢玩家账号。

安全策略
--------
1. **幂等**：所有 INSERT 都是 ``ON CONFLICT DO NOTHING`` 或带 ``WHERE ... IS NULL``，
   重复跑多少次结果一样。
2. **不删数据**：迁完把旧表 ``RENAME TO legacy_*``，不 DROP。发现不对可以改回去：
   ``ALTER TABLE legacy_accounts RENAME TO accounts;``
3. **保留主链**：只迁“还能对上”的行，对不上的（孤儿）跳过并计数，不中断。
4. 旧表不存在（新库 / 已迁过）时什么都不做，直接退出 0。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

import config  # noqa: E402

## 旧表 -> 改名后的名字
LEGACY_TABLES = {
    "devices": "legacy_devices",
    "accounts": "legacy_accounts",
    "players": "legacy_players",
    "world_items": "legacy_world_items",
}


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
    row = cur.fetchone()
    return bool(row[0])


def _count(cur, name: str) -> int:
    cur.execute(f"SELECT count(*) FROM {name}")     # 表名来自常量表，不拼用户输入
    return int(cur.fetchone()[0])


def main() -> int:
    parser = argparse.ArgumentParser(description="旧表数据迁移")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写入")
    args = parser.parse_args()

    cfg = config.load_config()
    if cfg.storage_is_memory:
        print("[skip] DB_BACKEND=memory，无需迁移")
        return 0
    if not cfg.database_url:
        print("[fail] DATABASE_URL 未配置")
        return 1

    from db import Database

    print(f"[..] 连接远端库：{cfg.database_url.split('@')[-1]}")
    database = Database(dsn=cfg.database_url)      # 顺手保证新表已建好
    if not database.ping():
        print("[fail] 数据库连不上")
        database.close()
        return 1

    report: list[tuple[str, int]] = []

    try:
        with database.cursor(dict_rows=False) as cur:
            present = {name: _table_exists(cur, name) for name in LEGACY_TABLES}
            if not any(present.values()):
                renamed = [n for n in LEGACY_TABLES.values() if _table_exists(cur, n)]
                if renamed:
                    print(f"[ok] 旧表已迁过（{', '.join(renamed)}），无需重复迁移")
                else:
                    print("[ok] 没有旧表，无需迁移")
                return 0
            print(f"[..] 检测到旧表：{', '.join(n for n, ok in present.items() if ok)}")

            if args.dry_run:
                for name, ok in present.items():
                    if ok:
                        print(f"  - {name}: {_count(cur, name)} 行 -> 将搬入新表并改名为 {LEGACY_TABLES[name]}")
                return 0

            # ---- 1. devices -> users （设备行，user_id 先留空） -----------------
            if present["devices"]:
                cur.execute(
                    """
                    INSERT INTO users (device_id, client_version, platform, last_ip,
                                       created_at, last_seen_at)
                    SELECT device_id,
                           COALESCE(client_version, ''),
                           COALESCE(platform, ''),
                           COALESCE(last_ip, ''),
                           COALESCE(created_at, now()),
                           COALESCE(last_seen_at, now())
                    FROM devices
                    ON CONFLICT (device_id) DO NOTHING
                    """
                )
                report.append(("devices -> users", cur.rowcount))

            # ---- 2. accounts -> users （把账号绑到已有设备行上） -----------------
            if present["accounts"]:
                cur.execute(
                    """
                    INSERT INTO users (device_id, user_id, user_name, status,
                                       locked_until, last_login_at)
                    SELECT device_id, user_id, user_name,
                           COALESCE(status, 'active'), locked_until, last_login_at
                    FROM accounts
                    ON CONFLICT (device_id) DO UPDATE SET
                        user_id       = EXCLUDED.user_id,
                        user_name     = EXCLUDED.user_name,
                        status        = EXCLUDED.status,
                        locked_until  = EXCLUDED.locked_until,
                        last_login_at = EXCLUDED.last_login_at
                    WHERE users.user_id IS NULL
                    """
                )
                report.append(("accounts -> users", cur.rowcount))

            # ---- 3. players -> player -------------------------------------------
            if present["players"]:
                cur.execute(
                    """
                    INSERT INTO player (user_id, player_name, race,
                                        pos_x, pos_y, pos_z, yaw, state, online,
                                        hp_max, hp, rev)
                    SELECT p.user_id,
                           COALESCE(u.user_name, ''),
                           'human',
                           COALESCE(p.pos_x, 0), COALESCE(p.pos_y, 0),
                           COALESCE(p.pos_z, 0), COALESCE(p.yaw, 0),
                           COALESCE(p.state, 'idle'), FALSE,
                           100, COALESCE(p.hp, 100), 0
                    FROM players p
                    JOIN users u ON u.user_id = p.user_id
                    ON CONFLICT (user_id) DO NOTHING
                    """
                )
                report.append(("players -> player", cur.rowcount))

            # ---- 4. world_items -> world_item -----------------------------------
            if present["world_items"]:
                cur.execute(
                    """
                    INSERT INTO world_item (id, world_id, kind,
                                            pos_x, pos_y, pos_z, yaw, state, rev)
                    SELECT id, 'default', COALESCE(kind, 'generic'),
                           COALESCE(pos_x, 0), COALESCE(pos_y, 0),
                           COALESCE(pos_z, 0), COALESCE(yaw, 0),
                           COALESCE(state, 'idle'), 0
                    FROM world_items
                    ON CONFLICT (id) DO NOTHING
                    """
                )
                report.append(("world_items -> world_item", cur.rowcount))

            # ---- 5. 旧表改名（不删） -----------------------------------------
            for old, new in LEGACY_TABLES.items():
                if not present[old]:
                    continue
                if _table_exists(cur, new):
                    print(f"[..] {new} 已存在，{old} 保留不动（请人工确认）")
                    continue
                cur.execute(f"ALTER TABLE {old} RENAME TO {new}")
                print(f"[ok] {old} -> {new}（数据保留，可随时改回）")

        print("\n[ok] 迁移完成：")
        for what, rows in report:
            print(f"  - {what}: {rows} 行")

        with database.cursor(dict_rows=False) as cur:
            print(f"\n[..] 新表行数：users={_count(cur, 'users')} "
                  f"player={_count(cur, 'player')} "
                  f"world={_count(cur, 'world')} "
                  f"world_item={_count(cur, 'world_item')}")
        return 0

    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"[fail] 迁移失败：{exc}")
        return 1
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())

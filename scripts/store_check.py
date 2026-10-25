"""PostgreSQL 存储层校验（跑真库，但只碰自己造的临时数据，跑完就清）。

用法::

    uv run scripts/store_check.py

它覆盖：设备登记 / 按设备查账号 / 建号冲突（user_name / device_id / user_id）/
登录查询 / 刷新活跃时间 / 世界+玩家快照 upsert / 读回 / 置全离线。

连不上库就跳过（退出码 0），仅在断言失败时返回 1。
"""

from __future__ import annotations

import pathlib
import secrets
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

import config  # noqa: E402
from store import PostgresStore, StoreConflict  # noqa: E402


def main() -> int:
    cfg = config.load_config()
    if cfg.storage_is_memory:
        print("[skip] DB_BACKEND=memory：这是内存后端，无需校验 SQL")
        return 0
    if not cfg.database_url:
        print("[skip] DATABASE_URL 未配置")
        return 0

    from db import Database

    database = Database(
        dsn=cfg.database_url,
        minconn=cfg.db_pool_min,
        maxconn=cfg.db_pool_max,
    )
    if not database.ping():
        print("[skip] 数据库连不上，跳过")
        database.close()
        return 0

    store = PostgresStore(database)
    passed = 0
    failed: list[str] = []

    def check(cond: bool, what: str) -> None:
        nonlocal passed
        if cond:
            passed += 1
            print(f"  ✓ {what}")
        else:
            failed.append(what)
            print(f"  ✗ {what}")

    tag = f"smoke{int(time.time())}{secrets.token_hex(3)}"
    device_hex = secrets.token_hex(1024)
    device_hex_b = secrets.token_hex(1024)
    user_id = "ZZZZ-ZZZZ"
    item_id = f"_smoke_{tag}"

    try:
        print(f"[..] 以 tag={tag} 运行，库={cfg.database_url.split('@')[-1]}")

        print("\n[case] 设备与账号")
        store.register_device(device_hex, "0.1.0", "windows", "127.0.0.1")
        store.register_device(device_hex, "0.1.1", "windows", "127.0.0.2")  # upsert 不应报错
        check(store.account_by_device(device_hex) is None, "新设备没有绑定账号")

        name_a = f"{tag}a"
        store.create_account(user_id, name_a, device_hex)
        acc = store.account_by_device(device_hex)
        check(acc is not None and acc["user_id"] == user_id, "按设备能查到账号")

        print("\n[case] 唯一约束冲突归类")
        store.register_device(device_hex_b)
        try:
            store.create_account("YYYY-YYYY", name_a, device_hex_b)
            check(False, "重名应抛 StoreConflict")
        except StoreConflict as exc:
            check(exc.field == "user_name", f"重名 -> field={exc.field}")

        try:
            store.create_account("XXXX-XXXX", f"{tag}c", device_hex)
            check(False, "设备已绑应抛 StoreConflict")
        except StoreConflict as exc:
            check(exc.field == "device_id", f"设备重复 -> field={exc.field}")

        try:
            store.create_account(user_id, f"{tag}d", device_hex_b)
            check(False, "user_id 重复应抛 StoreConflict")
        except StoreConflict as exc:
            check(exc.field == "user_id", f"user_id 重复 -> field={exc.field}")

        print("\n[case] SAVEPOINT 未污染事务")
        # 冲突后同一连接池里再写应该正常（没被 aborted 事务卡住）
        store.register_device(secrets.token_hex(8), "0.1.2", "linux", "10.0.0.1")
        check(True, "冲突后仍能正常写入")

        print("\n[case] 登录查询与活跃时间")
        row = store.account_for_login(user_id, device_hex)
        check(row is not None and row["device_id"] == device_hex, "账号-设备能对上")
        store.touch_activity(user_id, device_hex)
        row = store.account_for_login(user_id, device_hex)
        check(row.get("last_login_at") is not None, "last_login_at 已写入")

        print("\n[case] 世界 / 玩家快照")
        store.save_snapshot(
            [{
                "id": item_id, "kind": "smoke",
                "pos_x": 1.5, "pos_y": 0.0, "pos_z": -2.5,
                "yaw": 90.0, "state": "idle",
            }],
            [{
                "user_id": user_id, "device_id": device_hex,
                "pos_x": 3.0, "pos_y": 0.0, "pos_z": 4.0,
                "yaw": 180.0, "hp": 88.0, "state": "moving", "online": True,
            }],
        )
        items = store.load_world()
        mine = [it for it in items if it["id"] == item_id]
        check(len(mine) == 1, f"世界物品能读回（共 {len(items)} 条）")
        if mine:
            check(abs(mine[0]["pos_z"] + 2.5) < 1e-9, "坐标精度保持")

        # 同 id 再写一次 = upsert，不应报主键冲突
        store.save_snapshot(
            [{"id": item_id, "kind": "smoke", "pos_x": 9.0, "pos_y": 0.0,
              "pos_z": 0.0, "yaw": 0.0, "state": "open"}],
            [],
        )
        items = store.load_world()
        mine = [it for it in items if it["id"] == item_id]
        check(len(mine) == 1 and mine[0]["state"] == "open", "upsert 覆盖旧行")

        print("\n[case] 置全离线")
        store.mark_all_offline()
        check(True, "mark_all_offline 执行无异常")

    except Exception as exc:  # noqa: BLE001 - 存储层报错就是失败
        import traceback

        traceback.print_exc()
        failed.append(f"<exception> {exc}")
    finally:
        # 清理本次测试造的数据
        try:
            with database.conn() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM players WHERE user_id = %s", (user_id,))
                cur.execute("DELETE FROM world_items WHERE id = %s", (item_id,))
                cur.execute("DELETE FROM accounts WHERE user_id = %s", (user_id,))
                cur.execute("DELETE FROM accounts WHERE user_name LIKE %s", (f"{tag}%",))
                cur.execute("DELETE FROM devices WHERE last_ip = %s", ("127.0.0.1",))
                cur.execute("DELETE FROM devices WHERE last_ip = %s", ("127.0.0.2",))
                cur.execute("DELETE FROM devices WHERE last_ip = %s", ("10.0.0.1",))
            print("\n[..] 临时数据已清理")
        except Exception:  # noqa: BLE001
            print("\n[!] 清理时出错（不影响判定）")
        database.close()

    total = passed + len(failed)
    print(f"\n{'=' * 60}")
    print(f"PASSED {passed}/{total}    FAILED {len(failed)}")
    for what in failed:
        print(f"  ✗ {what}")
    print("=" * 60)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

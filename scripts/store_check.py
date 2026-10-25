"""PostgreSQL 存储层校验（跑真库，但只碰自己造的临时数据，跑完就清）。

用法::

    uv run scripts/store_check.py

它覆盖：

1. users：设备登记 / 按设备查账号 / 建号冲突归类（user_name / device_id / user_id）/ 登录查询 / 刷新时间
2. world + world_item：落库 / 读回 / **rev 门禁**（落后被拒、相等被拒、领先才写）
3. player：落库 / 读回（含三围）/ **rev 门禁** / 置全离线

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

    target = cfg.database_url.split("@")[-1]
    print(f"[..] 连接远端库：{target}")
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
            print(f"  \u2713 {what}")
        else:
            failed.append(what)
            print(f"  \u2717 {what}")

    tag = f"chk{int(time.time())}{secrets.token_hex(2)}"
    device_a = secrets.token_hex(16)
    device_b = secrets.token_hex(16)
    user_id = f"{tag[:4].upper()}-{tag[4:8].upper()}"
    name_a = f"{tag}a"
    world_id = f"_chk_{tag}"
    item_id = f"_chk_item_{tag}"
    ip_a, ip_b = "127.0.0.1", "127.0.0.2"

    try:
        # ---------------------------------------------------------------- #
        print("\n[case] users：设备 / 账号")
        store.touch_device(device_a, "0.1.0", "windows", ip_a)
        store.touch_device(device_a, "0.1.1", "windows", ip_b)   # upsert 不应报错
        check(store.account_by_device(device_a) is None, "新设备还没有绑定账号")

        store.create_user(user_id, name_a, device_a)
        acc = store.account_by_device(device_a)
        check(acc is not None and acc["user_id"] == user_id, "按设备能查到账号")

        store.touch_device(device_b, "0.1.0", "linux", ip_b)

        print("\n[case] 唯一约束冲突归类")
        try:
            store.create_user(f"{tag[:4].upper()}-AAAA", name_a, device_b)
            check(False, "重名应抛 StoreConflict")
        except StoreConflict as exc:
            check(exc.field == "user_name", f"重名 -> field={exc.field}")

        try:
            store.create_user(f"{tag[:4].upper()}-BBBB", f"{tag}c", device_a)
            check(False, "设备已绑应抛 StoreConflict")
        except StoreConflict as exc:
            check(exc.field == "device_id", f"设备重复 -> field={exc.field}")

        try:
            store.create_user(user_id, f"{tag}d", device_b)
            check(False, "user_id 重复应抛 StoreConflict")
        except StoreConflict as exc:
            check(exc.field == "user_id", f"user_id 重复 -> field={exc.field}")

        print("\n[case] SAVEPOINT 未污染事务")
        store.touch_device(secrets.token_hex(8), "0.1.2", "linux", "10.0.0.1")
        check(True, "冲突后仍能正常写入")

        print("\n[case] 登录查询与活跃时间")
        row = store.user_for_login(user_id)
        check(row is not None and row["device_id"] == device_a, "账号-设备能对上")
        store.touch_login(user_id, device_a)
        row = store.user_for_login(user_id)
        check(row.get("last_login_at") is not None, "last_login_at 已写入")

        # ---------------------------------------------------------------- #
        print("\n[case] world / world_item：落库 + rev 门禁")
        result = store.save_world(
            {"id": world_id, "name": f"检查世界 {tag}", "seed": 12345, "rev": 1},
            [{
                "id": item_id, "world_id": world_id, "kind": "check",
                "pos_x": 1.5, "pos_y": 0.0, "pos_z": -2.5,
                "yaw": 90.0, "state": "idle", "rev": 5,
            }],
        )
        check(result["written"] == 2 and not result["stale"],
              f"首写 world+item（written={result['written']}）")

        data = store.load_world(world_id)
        check(data.get("world") is not None and int(data["world"]["seed"]) == 12345,
              "世界元信息能读回")
        mine = [it for it in data.get("items") or [] if it["id"] == item_id]
        check(len(mine) == 1 and abs(mine[0]["pos_z"] + 2.5) < 1e-9, "实体坐标精度保持")

        stale_write = store.save_world(
            None,
            [{"id": item_id, "world_id": world_id, "kind": "check",
              "pos_x": 99.0, "pos_y": 0.0, "pos_z": 0.0,
              "yaw": 0.0, "state": "open", "rev": 3}],
        )
        check(stale_write["written"] == 0 and item_id in stale_write["stale"],
              "落后数据被拒（written=0 / stale 命中）")
        mine = [it for it in store.load_world(world_id).get("items") or []
                if it["id"] == item_id]
        check(bool(mine) and mine[0]["state"] == "idle", "落后写入没有改到库（仍为 idle）")

        same_write = store.save_world(
            None,
            [{"id": item_id, "world_id": world_id, "kind": "check",
              "pos_x": 99.0, "pos_y": 0.0, "pos_z": 0.0,
              "yaw": 0.0, "state": "open", "rev": 5}],
        )
        check(same_write["written"] == 0, "rev 相等也被拒（必顶更严格）")

        ahead_write = store.save_world(
            None,
            [{"id": item_id, "world_id": world_id, "kind": "check",
              "pos_x": 9.0, "pos_y": 0.0, "pos_z": 0.0,
              "yaw": 0.0, "state": "open", "rev": 6}],
        )
        check(ahead_write["written"] == 1 and not ahead_write["stale"], "领先数据能写入")
        mine = [it for it in store.load_world(world_id).get("items") or []
                if it["id"] == item_id]
        check(bool(mine) and mine[0]["state"] == "open"
              and abs(mine[0]["pos_x"] - 9.0) < 1e-9, "领先数据已覆盖（open / x=9）")

        # ---------------------------------------------------------------- #
        print("\n[case] player：三围 + rev 门禁")
        player_row = {
            "user_id": user_id,
            "player_name": f"角色{tag}",
            "race": "elf",
            "pos_x": 3.0, "pos_y": 0.0, "pos_z": 4.0, "yaw": 180.0,
            "state": "moving", "online": True,
            "hp_max": 120.0, "hp": 88.0,
            "stamina_max": 90.0, "stamina": 45.5,
            "mana_max": 200.0, "mana": 33.0,
            "rev": 1,
        }
        saved = store.save_players([player_row])
        check(saved["written"] == 1 and not saved["stale"], "玩家行首写成功")

        rows = [p for p in store.load_players() if p["user_id"] == user_id]
        check(len(rows) == 1, "玩家行能读回")
        if rows:
            got = rows[0]
            check(got["race"] == "elf" and abs(got["hp"] - 88.0) < 1e-9
                  and abs(got["stamina"] - 45.5) < 1e-9 and abs(got["mana"] - 33.0) < 1e-9,
                  "三围与种族读回一致")
            check(got.get("user_name") == name_a, "JOIN 带回账号名")

        stale_player = dict(player_row, hp=1.0, rev=0)
        res = store.save_players([stale_player])
        check(res["written"] == 0 and user_id in res["stale"], "玩家落后数据被拒")
        rows = [p for p in store.load_players() if p["user_id"] == user_id]
        check(bool(rows) and abs(rows[0]["hp"] - 88.0) < 1e-9, "玩家落后写入未改库（hp=88）")

        ahead_player = dict(player_row, hp=42.0, rev=2)
        res = store.save_players([ahead_player])
        check(res["written"] == 1, "玩家领先数据能写入")
        rows = [p for p in store.load_players() if p["user_id"] == user_id]
        check(bool(rows) and abs(rows[0]["hp"] - 42.0) < 1e-9, "玩家领先数据已覆盖（hp=42）")

        print("\n[case] 置全离线")
        store.mark_all_offline()
        rows = [p for p in store.load_players() if p["user_id"] == user_id]
        check(bool(rows) and rows[0]["online"] is False, "online 已清成 FALSE")

    except Exception as exc:  # noqa: BLE001 - 存储层报错就是失败
        import traceback

        traceback.print_exc()
        failed.append(f"<exception> {exc}")
    finally:
        # 清理本次测试造的数据（按 tag / ip 限定，不碰其它行）
        try:
            with database.conn() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM player WHERE user_id = %s", (user_id,))
                cur.execute("DELETE FROM world_item WHERE world_id = %s", (world_id,))
                cur.execute("DELETE FROM world WHERE id = %s", (world_id,))
                cur.execute("DELETE FROM users WHERE user_name LIKE %s", (f"{tag}%",))
                cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
                cur.execute("DELETE FROM users WHERE last_ip = %s", (ip_a,))
                cur.execute("DELETE FROM users WHERE last_ip = %s", (ip_b,))
                cur.execute("DELETE FROM users WHERE last_ip = %s", ("10.0.0.1",))
            print("\n[..] 临时数据已清理")
        except Exception:  # noqa: BLE001
            print("\n[!] 清理时出错（不影响判定）")
        database.close()

    total = passed + len(failed)
    print(f"\n{'=' * 60}")
    print(f"PASSED {passed}/{total}    FAILED {len(failed)}")
    for what in failed:
        print(f"  \u2717 {what}")
    print("=" * 60)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

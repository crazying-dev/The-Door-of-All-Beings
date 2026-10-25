"""建表 / 升级脚本（幂等，可重复执行）。

用法::

    uv run scripts/init_db.py

连接串取自 `.env` 的 ``DATABASE_URL``；表结构见 `schema.sql`。
``DB_BACKEND=memory`` 时什么也不做（内存后端不需要建表）。
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from db import Database  # noqa: E402


def main() -> int:
    cfg = config.load_config()
    if cfg.storage_is_memory:
        print("[skip] DB_BACKEND=memory：内存后端不需要建表")
        return 0
    if not cfg.database_url:
        print("[fail] DATABASE_URL 未配置：复制 .env.example 为 .env 并填写")
        return 1

    print(f"[..] 连接 {cfg.database_url.split('@')[-1]}")
    database = Database(
        dsn=cfg.database_url,
        minconn=cfg.db_pool_min,
        maxconn=cfg.db_pool_max,
    )
    try:
        if not database.ping():
            print("[fail] 数据库连不上")
            return 1
        print("[ok] 表结构已就绪（devices / accounts / world_items / players）")
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())


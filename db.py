"""PostgreSQL 连接池与初始化。"""

from __future__ import annotations

import atexit
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor

log = logging.getLogger(__name__)

## schema.sql 就在本文件旁边（目录提升后不再需要上跳一层）
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class Database:
    """
    PostgreSQL 连接池与初始化。

    配置来源(.env):
      DATABASE_URL  连接串
      DB_POOL_MIN   最小连接数(默认 1)
      DB_POOL_MAX   最大连接数(默认 8)
    """

    def __init__(
        self,
        dsn: str | None = None,
        minconn: int | None = None,
        maxconn: int | None = None,
        init_schema: bool = True,
    ) -> None:
        dsn = (dsn if dsn is not None else os.getenv("DATABASE_URL", "")).strip()
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL 未配置（请复制 .env.example 为 .env 并填写）"
            )
        minconn = minconn if minconn is not None else int(os.getenv("DB_POOL_MIN", "1"))
        maxconn = maxconn if maxconn is not None else int(os.getenv("DB_POOL_MAX", "8"))

        # 创建线程安全连接池
        self._pool = pg_pool.ThreadedConnectionPool(minconn, maxconn, dsn)

        # 进程退出时自动关闭连接池
        atexit.register(self.close)

        # 初始化表结构(幂等, 可重复执行)
        if init_schema:
            self._init_schema()


    # ------------------------------------------------------------------ #
    # 连接/游标借用
    # ------------------------------------------------------------------ #

    @contextmanager
    def conn(self) -> Iterator[Any]:
        """借出一个连接；正常退出时 commit，异常时 rollback。"""
        conn = self._pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    @contextmanager
    def cursor(self, dict_rows: bool = True) -> Iterator[Any]:
        """借出一个游标；DictCursor 返回 dict，否则返回 tuple。"""
        with self.conn() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor if dict_rows else None)
            try:
                yield cur
            finally:
                cur.close()

    # ------------------------------------------------------------------ #
    # 工具方法
    # ------------------------------------------------------------------ #

    def _init_schema(self) -> None:
        """执行 schema.sql（users / player / world / world_item）。

        库在**远端**（`.env` 的 DATABASE_URL），本方法在连接后自动跑一次；
        schema.sql 本身是幂等的（``CREATE TABLE IF NOT EXISTS`` +
        ``ADD COLUMN IF NOT EXISTS``），重启/多进程并发跑都不会报错。
        """
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.cursor(dict_rows=False) as cur:
            cur.execute(sql)
        log.info("数据库表结构已就绪 (%s)", SCHEMA_PATH.name)


    def ping(self) -> bool:
        """数据库可用返回 True，不抛异常。"""
        try:
            with self.cursor(dict_rows=False) as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:  # noqa: BLE001 - 探活失败就是 False，不要让调用方处理
            return False


    def close(self) -> None:
        """关闭连接池（幂等：atexit 与服务退出都会调，不能重复关）。"""
        pool = getattr(self, "_pool", None)
        if pool is None:
            return
        self._pool = None
        try:
            pool.closeall()
        except Exception:  # noqa: BLE001 - 已经关过就别再抱错
            pass

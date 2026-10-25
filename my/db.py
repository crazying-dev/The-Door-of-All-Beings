"""PostgreSQL 连接池与初始化。"""

import atexit
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor


class Database:
    """
    PostgreSQL 连接池与初始化。

    配置来源(.env):
      DATABASE_URL  连接串
      DB_POOL_MIN   最小连接数(默认 1)
      DB_POOL_MAX   最大连接数(默认 8)
    """

    def __init__(self):
        dsn = os.getenv("DATABASE_URL", "").strip()
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL 未配置（请复制 .env.example 为 .env 并填写）"
            )
        minconn = int(os.getenv("DB_POOL_MIN", "1"))
        maxconn = int(os.getenv("DB_POOL_MAX", "8"))

        # 创建线程安全连接池
        self._pool = pg_pool.ThreadedConnectionPool(minconn, maxconn, dsn)

        # 进程退出时自动关闭连接池
        atexit.register(self.close)

        # 初始化表结构(幂等, 可重复执行)
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
        """执行 server/schema.sql（devices / accounts / world_items）。"""
        schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
        sql = schema_path.read_text(encoding="utf-8")
        with self.cursor(dict_rows=False) as cur:
            cur.execute(sql)

    def ping(self) -> bool:
        """数据库可用返回 True，不抛异常。"""
        try:
            with self.cursor(dict_rows=False) as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            return False

    def close(self) -> None:
        """关闭连接池（由 atexit 在服务退出时自动调用，也可手动调用）。"""
        self._pool.closeall()

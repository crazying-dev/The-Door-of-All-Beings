"""PostgreSQL 连接池、建表与健康检查。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from .config import Config

_POOL: pool.ThreadedConnectionPool | None = None


class DatabaseUnavailable(RuntimeError):
    """数据库不可用（未配置或连接失败）。"""


def init_pool(
    dsn: str | None = None,
    minconn: int | None = None,
    maxconn: int | None = None,
) -> None:
    """初始化全局连接池（幂等）。"""
    global _POOL
    if _POOL is not None:
        return
    dsn = (dsn or Config.DATABASE_URL).strip()
    if not dsn:
        raise DatabaseUnavailable("DATABASE_URL 未配置（请复制 .env.example 为 .env 并填写）")
    _POOL = pool.ThreadedConnectionPool(
        minconn if minconn is not None else Config.DB_POOL_MIN,
        maxconn if maxconn is not None else Config.DB_POOL_MAX,
        dsn,
    )


def close_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.closeall()
        _POOL = None


@contextmanager
def get_conn() -> Iterator[Any]:
    """借出一个连接；正常退出时 commit，异常时 rollback。"""
    if _POOL is None:
        init_pool()
    assert _POOL is not None
    conn = _POOL.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _POOL.putconn(conn)


@contextmanager
def get_cursor(dict_rows: bool = True) -> Iterator[Any]:
    """借出一个游标；DictCursor 返回 dict，否则返回 tuple。"""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor if dict_rows else None)
        try:
            yield cur
        finally:
            cur.close()


def init_schema() -> None:
    """执行 schema.sql（幂等）。"""
    schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    with get_cursor(dict_rows=False) as cur:
        cur.execute(sql)


def ping() -> bool:
    """数据库可用返回 True，不抛异常。"""
    try:
        with get_cursor(dict_rows=False) as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        return False

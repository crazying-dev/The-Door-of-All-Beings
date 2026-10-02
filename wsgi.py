"""WSGI 入口：waitress-serve --call 或 wsgi:app 使用。

运行（uv 会自动创建 .venv 并安装依赖）：
    uv run waitress-serve --host 127.0.0.1 --port 2690 wsgi:app
"""

from __future__ import annotations

from app import create_app, db
from app.config import Config

if Config.DATABASE_URL:
    try:
        db.init_pool()
        db.init_schema()
    except Exception as exc:  # noqa: BLE001
        import sys

        print(f"[db] 初始化失败：{exc!r}", file=sys.stderr, flush=True)

app = create_app()

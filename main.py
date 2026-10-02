"""众生之门 服务端入口（开发用：flask 自带服务器）。

生产建议用 waitress：
    uv run waitress-serve --host 127.0.0.1 --port 2690 wsgi:app
"""

from __future__ import annotations

import sys

from app import db
from app.config import Config


def bootstrap() -> None:
    """连接数据库并保证表结构存在（幂等）。"""
    if not Config.DATABASE_URL:
        print("[warn] DATABASE_URL 未配置：复制 .env.example 为 .env 并填写", file=sys.stderr, flush=True)
        return
    try:
        db.init_pool()
        db.init_schema()
        print("[db] 连接成功，表结构已就绪", flush=True)
    except Exception as exc:  # noqa: BLE001 - 启动时只警告，不阻断
        print(f"[db] 初始化失败：{exc!r}", file=sys.stderr, flush=True)


def main() -> int:
    bootstrap()
    from app import create_app

    app = create_app()
    print(f"[web] http://{Config.HOST}:{Config.PORT}", flush=True)
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

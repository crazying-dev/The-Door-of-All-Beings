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

# 世界状态：回灌世界物品位置 + 启动广播线程（服务端主动推位置）
# 注意：WS 需要能拿到底层 socket 的 WSGI 服务器（werkzeug 开发服务器 / gunicorn --threads），
# waitress 不支持 WebSocket，用 waitress 部署时只有 HTTP 快照接口可用。
from app.world import persist  # noqa: E402
from app.world.state import get_state  # noqa: E402
from app.world.ws import start_broadcaster  # noqa: E402

_WORLD = get_state()
_WORLD_LOADED = persist.load(_WORLD)
start_broadcaster(app)


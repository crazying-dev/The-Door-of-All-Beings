"""建表脚本（幂等）。

用法：uv run scripts/init_db.py（uv 会自动建 .venv 并安装依赖）
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import Config  # noqa: E402


def main() -> int:
    if not Config.DATABASE_URL:
        print("DATABASE_URL 未配置：复制 .env.example 为 .env 并填写")
        return 1
    db.init_pool()
    db.init_schema()
    print("[ok] 数据库连接正常，表结构已就绪（devices / accounts）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

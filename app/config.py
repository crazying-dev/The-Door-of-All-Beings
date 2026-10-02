"""从 .env / 环境变量读取配置。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


class Config:
    SERVICE_NAME = os.getenv("SERVICE_NAME", "the-door-of-bings")
    SERVICE_VERSION = os.getenv("SERVICE_VERSION", "1.0.0")

    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
    DB_POOL_MIN = _int_env("DB_POOL_MIN", 1)
    DB_POOL_MAX = _int_env("DB_POOL_MAX", 8)

    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = _int_env("PORT", 2690)
    DEBUG = os.getenv("DEBUG", "0") == "1"

    # 用户名称长度上限
    USER_NAME_MAX_LEN = _int_env("USER_NAME_MAX_LEN", 24)

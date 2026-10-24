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


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}



class Config:
    SERVICE_NAME = os.getenv("SERVICE_NAME", "the-door-of-bings")
    SERVICE_VERSION = os.getenv("SERVICE_VERSION", "1.1.0")

    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
    DB_POOL_MIN = _int_env("DB_POOL_MIN", 1)
    DB_POOL_MAX = _int_env("DB_POOL_MAX", 8)

    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = _int_env("PORT", 2690)
    DEBUG = os.getenv("DEBUG", "0") == "1"

    # 用户名称长度上限
    USER_NAME_MAX_LEN = _int_env("USER_NAME_MAX_LEN", 24)

    # ---------------------------------------------------------------- #
    # 世界状态（玩家列表 / 世界物品列表，WebSocket 实时同步）
    # ---------------------------------------------------------------- #
    # 广播节拍：每秒下发几次快照（只在有变化时发，心跳另算）
    WORLD_TICK_HZ = _int_env("WORLD_TICK_HZ", 10)
    # 无变化时的心跳广播间隔（秒），让客户端能确认连接还活着
    WORLD_HEARTBEAT_SEC = _float_env("WORLD_HEARTBEAT_SEC", 2.0)
    # 连接存活判定：超过这个秒数没有连接就直接从列表里踢掉
    WORLD_STALE_SEC = _float_env("WORLD_STALE_SEC", 60.0)
    # 是否校验「设备 + 用户 ID + 名称」三元组（生产必须为 1）
    WORLD_REQUIRE_AUTH = _bool_env("WORLD_REQUIRE_AUTH", True)
    # 世界物品位置是否落库（需要 DATABASE_URL）
    WORLD_PERSIST = _bool_env("WORLD_PERSIST", True)
    WORLD_FLUSH_SEC = _float_env("WORLD_FLUSH_SEC", 10.0)
    # 同时在线的最大连接数
    WORLD_MAX_CLIENTS = _int_env("WORLD_MAX_CLIENTS", 256)

    # WebSocket 细节
    WS_HANDSHAKE_TIMEOUT = _float_env("WS_HANDSHAKE_TIMEOUT", 10.0)
    WS_IDLE_TIMEOUT = _float_env("WS_IDLE_TIMEOUT", 75.0)
    # 单个客户端上报位置的最小间隔（秒），相当于每个连接 50Hz 上限
    WS_MIN_UPDATE_INTERVAL = _float_env("WS_MIN_UPDATE_INTERVAL", 0.02)


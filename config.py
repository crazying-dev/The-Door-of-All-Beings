"""集中配置：所有可变项都来自环境变量 / .env。

业务代码不再直接 ``os.getenv``，换部署环境只改 `.env`。

环境变量一览
------------
================================  ==========================================
DATABASE_URL                      PostgreSQL 连接串
DB_BACKEND                        postgres（默认）/ memory（本地调试、跑测试）
DB_POOL_MIN / DB_POOL_MAX         连接池上下限（1 / 8）
HOST                              **对外公布**的地址（HTTP 发现接口下发）
PORT                              **对外公布**的 TCP 端口（默认 5423）
TCP_BIND                          TCP 实际监听地址（默认 0.0.0.0）
HTTP_BIND                          HTTP 实际监听地址（默认 0.0.0.0）
HTTP_PORT                          HTTP 端口（默认 4231）
DEBUG                             1/true/on 开启调试（调试时 HTTP 走 Werkzeug）
DEVICE_ID_SECRET                  设备 ID 的 HMAC 密钥（≥32B，服务端签发用）
DEVICE_ID_MAX_AGE                 device_id 有效期秒数，0 = 永不过期（默认 0）
REQUEST_TIME_WINDOW               报文时间戳容差秒数（默认 300）
MAX_LINE_BYTES                    单行最大字节数（默认 16384）
READ_TIMEOUT                      连接读超时秒数（默认 180）
MAX_CONNECTIONS                   最大并发连接数（默认 256）
PUSH_HZ                           世界快照推送频率（默认 10）
AUTOSAVE_INTERVAL                 内存数据落库间隔秒数（默认 60）
USER_NAME_MAX_LEN                 用户名称最大长度（默认 24）
==================================  ==========================================

注意：`HOST` 只用于「告诉客户端该连哪里」，不再参与「是否启动 TCP」的判断
（旧版把 ``os.getenv("HOST") == 公网IP`` 当开关，出口 IP 一变服务就静默不启动）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

## 协议版本：报文第一行的 version 字段
PROTOCOL_VERSION = "1.1"
## 服务端软件版本：随发布递增，客户端可据此提示更新
SERVICE_VERSION = "2.0.0"

## 工程根目录（本文件所在目录），用于定位 .env / schema.sql
BASE_DIR = Path(__file__).resolve().parent

_TRUE = {"1", "true", "yes", "on"}


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env_str(key)
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env_str(key).lower()
    if raw == "":
        return default
    return raw in _TRUE


@dataclass(frozen=True)
class Config:
    """一次进程生命周期内不变的配置快照。"""

    # --- 数据库 ---------------------------------------------------------
    database_url: str
    db_backend: str
    db_pool_min: int
    db_pool_max: int

    # --- 网络 -----------------------------------------------------------
    public_host: str
    public_port: int
    tcp_bind: str
    tcp_port: int
    http_bind: str
    http_port: int

    # --- 安全 -----------------------------------------------------------
    device_id_secret: str
    device_id_max_age: int
    request_time_window: int

    # --- 运行时 ---------------------------------------------------------
    debug: bool
    max_line_bytes: int
    read_timeout: int
    max_connections: int
    push_hz: int
    autosave_interval: int
    user_name_max_len: int

    @property
    def storage_is_memory(self) -> bool:
        return self.db_backend == "memory"

    def describe(self) -> str:
        """一行摘要，启动日志用（不含任何密钥）。"""
        return (
            f"service={SERVICE_VERSION} protocol={PROTOCOL_VERSION} "
            f"tcp={self.tcp_bind}:{self.tcp_port} "
            f"http={self.http_bind}:{self.http_port} "
            f"public={self.public_host}:{self.public_port} "
            f"db={self.db_backend} push={self.push_hz}Hz"
        )


def load_config(env_path: Path | None = None) -> Config:
    """读取 .env（不覆盖已有环境变量）并归集成 Config。"""
    path = env_path or (BASE_DIR / ".env")
    if path.exists():
        load_dotenv(path, override=False)

    public_port = _env_int("PORT", 5423)
    tcp_port = _env_int("TCP_PORT", public_port)
    public_host = _env_str("HOST") or _env_str("PUBLIC_HOST")

    return Config(
        database_url=_env_str("DATABASE_URL"),
        db_backend=(_env_str("DB_BACKEND", "postgres").lower() or "postgres"),
        db_pool_min=_env_int("DB_POOL_MIN", 1),
        db_pool_max=_env_int("DB_POOL_MAX", 8),
        public_host=public_host,
        public_port=public_port,
        tcp_bind=_env_str("TCP_BIND", "0.0.0.0") or "0.0.0.0",
        tcp_port=tcp_port,
        http_bind=_env_str("HTTP_BIND", "0.0.0.0") or "0.0.0.0",
        http_port=_env_int("HTTP_PORT", 4231),
        device_id_secret=_env_str("DEVICE_ID_SECRET"),
        device_id_max_age=_env_int("DEVICE_ID_MAX_AGE", 0),
        request_time_window=_env_int("REQUEST_TIME_WINDOW", 300),
        debug=_env_bool("DEBUG", False),
        max_line_bytes=_env_int("MAX_LINE_BYTES", 16384),
        read_timeout=_env_int("READ_TIMEOUT", 180),
        max_connections=_env_int("MAX_CONNECTIONS", 256),
        push_hz=_env_int("PUSH_HZ", 10),
        autosave_interval=_env_int("AUTOSAVE_INTERVAL", 60),
        user_name_max_len=_env_int("USER_NAME_MAX_LEN", 24),
    )

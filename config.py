"""集中配置：所有可变项都来自环境变量 / .env。

业务代码不再直接 ``os.getenv``，换部署环境只改 `.env`。

本组件是 **HTTP 发现服务**（TCP 游戏协议归 server-socket 组件），
所以这里只剩发现接口需要的东西：对外公布的 TCP 地址、HTTP 监听、调试开关。

环境变量一览
------------
================================  ==========================================
HOST                              **对外公布**的地址（发现接口下发给客户端）
PORT                              **对外公布**的 TCP 端口（默认 5423）
HTTP_BIND                         HTTP 实际监听地址（默认 0.0.0.0）
HTTP_PORT                         HTTP 端口（默认 4231）
DEBUG                             1/true/on 开启调试（调试时 HTTP 走 Werkzeug）
==================================  ==========================================

注意：`HOST` 只用于「告诉客户端该连哪里」，不再参与「是否启动」的判断
（旧版把 ``os.getenv("HOST") == 公网IP`` 当开关，出口 IP 一变服务就静默不启动）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

## 协议版本：TCP 游戏协议的版本，发现接口下发给客户端做兼容提示
## 必须与 server-socket 组件的 PROTOCOL_VERSION 保持一致（1.3 = 无主动推送 / 拉取式 + 头+正文联合哈希）
PROTOCOL_VERSION = "1.3"
## 服务端软件版本：随发布递增，客户端可据此提示更新
## 必须与 server-socket 组件的 SERVICE_VERSION 保持一致
SERVICE_VERSION = "4.0.0"

## 工程根目录（本文件所在目录），用于定位 .env
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

    # --- 网络 -----------------------------------------------------------
    public_host: str
    public_port: int
    http_bind: str
    http_port: int

    # --- 运行时 ---------------------------------------------------------
    debug: bool

    def describe(self) -> str:
        """一行摘要，启动日志用（不含任何密钥）。"""
        return (
            f"service={SERVICE_VERSION} protocol={PROTOCOL_VERSION} "
            f"http={self.http_bind}:{self.http_port} "
            f"public={self.public_host}:{self.public_port}"
        )


def load_config(env_path: Path | None = None) -> Config:
    """读取 .env（不覆盖已有环境变量）并归集成 Config。"""
    path = env_path or (BASE_DIR / ".env")
    if path.exists():
        load_dotenv(path, override=False)

    public_port = _env_int("PORT", 5423)
    public_host = _env_str("HOST") or _env_str("PUBLIC_HOST")

    return Config(
        public_host=public_host,
        public_port=public_port,
        http_bind=_env_str("HTTP_BIND", "0.0.0.0") or "0.0.0.0",
        http_port=_env_int("HTTP_PORT", 4231),
        debug=_env_bool("DEBUG", False),
    )


# ---------------------------------------------------------------------------
# 环境变量回写（启动流程 s1 的第一步）
# ---------------------------------------------------------------------------

## `export_env()` 会回写进程环境的键
ENV_KEYS = (
    "HOST", "PORT",
    "HTTP_BIND", "HTTP_PORT", "DEBUG",
)


def export_env(cfg: Config) -> list[str]:
    """把**规范化后**的配置回写 ``os.environ``（s1 初始化第 ① 步）。

    为什么要回写：`.env` 里可以写 ``PORT=5423``、也可以完全不写（默认值），
    回写之后全进程只存在一份值，不会再出现「config 读 5423、别处读默认」的情况。

    返回真正发生变化的键（已经是规范值的不算）。
    """
    values = {
        "HOST": cfg.public_host,
        "PORT": str(cfg.public_port),
        "HTTP_BIND": cfg.http_bind,
        "HTTP_PORT": str(cfg.http_port),
        "DEBUG": "1" if cfg.debug else "0",
    }
    written: list[str] = []
    for key in ENV_KEYS:
        value = values.get(key)
        if value is None:
            continue
        if os.environ.get(key) != value:
            os.environ[key] = value
            written.append(key)
    return written

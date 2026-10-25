"""服务端入口。

用法::

    python main.py               # TCP + HTTP（默认）
    python main.py serve         # 同上
    python main.py tcp           # 只起 TCP 游戏协议
    python main.py http          # 只起 HTTP 发现/健康检查
    python main.py check         # 自检：配置 / 存储 / 设备密钥 / 依赖
    python main.py version

本地不想接数据库时：``DB_BACKEND=memory python main.py``
（数据只存在内存，重启即消失）。
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
import threading

import actions
import config
import device_id as devid
import game
import http_api
import protocol
import server as server_mod
import store as store_mod
from session import SessionHub
from users import user as UserBusiness

log = logging.getLogger("server")


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def setup_logging(debug: bool = False) -> None:
    """UTF-8 安全地配日志（Windows 控制台默认 GBK，中文直接打会乱码/报错）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S"
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    # waitress / werkzeug 的启动日志太啰嗝，降一级
    for noisy in ("waitress", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 服务装配
# ---------------------------------------------------------------------------

class Service:
    """一整套依赖的持有者（测试脚本也直接用它）。"""

    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self._closed = False
        self._prepare_secret()

        self.store = store_mod.build_store(cfg)
        self.users = UserBusiness(self.store, cfg.user_name_max_len)
        self.hub = SessionHub(push_hz=cfg.push_hz, max_connections=cfg.max_connections)

        actions.init(cfg, self.store, self.users, self.hub)

        game.load_from_store(self.store)
        game.start_autosave(self.store, cfg.autosave_interval)

        self.server = server_mod.GameServer(cfg, self.store, self.users, self.hub)
        self.app = http_api.create_app(cfg, self.store, self.server)

    # ------------------------------------------------------------------ #

    def _prepare_secret(self) -> None:
        """设备 ID 签名密钥。

        没配就拒启，而不是默默随机一个：一重启设备 ID 全失效，
        所有老账号都绑不上了（客户端存的旧 ID 会被判非法）。
        """
        secret = self.cfg.device_id_secret
        if secret:
            if len(secret.encode("utf-8")) < devid.MIN_SECRET_BYTES:
                raise SystemExit(
                    f"DEVICE_ID_SECRET 太短（至少 {devid.MIN_SECRET_BYTES} 字节）"
                )
            devid.configure(secret)
            return

        if self.cfg.storage_is_memory:
            # 内存后端本来就是一次性数据，临时密钥不会造成真实损失
            devid.configure(secrets.token_hex(32))
            log.warning("DEVICE_ID_SECRET 未配置，已用临时密钥（仅 DB_BACKEND=memory 允许）")
            return

        raise SystemExit(
            "DEVICE_ID_SECRET 未配置。\n"
            "请在 server/.env 里加一行（任选一种生成方式）：\n"
            "  python -c \"import secrets;print('DEVICE_ID_SECRET='+secrets.token_hex(32))\"\n"
            "然后重启。这个值一旦定下就不要改：设备 ID 由它签名，换了老账号会绑不上。"
        )

    # ------------------------------------------------------------------ #

    def check(self) -> bool:
        """自检：不监听端口，只确认各个部件能用。"""
        ok = True
        log.info("配置：%s", self.cfg.describe())
        log.info("协议：%s（接受 %s）", protocol.CURRENT_VERSION, protocol.SUPPORTED_VERSIONS)
        log.info("设备 ID 密钥：%s", "已配置" if devid.has_secret() else "缺失")

        if not devid.has_secret():
            ok = False
        else:
            sample = devid.DeviceId_get(devid.DEFAULT_LENGTH)
            good = devid.DeviceId_can_use(sample)
            tampered = bytearray(sample)
            tampered[-1] ^= 0xFF
            bad = devid.DeviceId_can_use(bytes(tampered))
            log.info("设备 ID 自检：正常=%s 篡改被拒=%s", good, not bad)
            ok = ok and good and not bad

        if self.store.ping():
            log.info("存储可用：backend=%s", self.store.backend_name)
        else:
            log.error("存储不可用：backend=%s", self.store.backend_name)
            ok = False

        log.info("已注册动作：%s", ", ".join(sorted(actions.ACTIONS)))
        return ok

    # ------------------------------------------------------------------ #

    def run(self, mode: str = "serve") -> None:
        """启动服务（阻塞）。"""
        if mode == "check":
            sys.exit(0 if self.check() else 1)

        if mode in ("serve", "tcp"):
            self.server.bind()
            self.hub.start_pusher()

        try:
            if mode == "tcp":
                self.server.serve_forever()
            elif mode == "http":
                log.info("HTTP 监听 %s:%s", self.cfg.http_bind, self.cfg.http_port)
                http_api.serve(self.cfg, self.app)
            else:
                threading.Thread(
                    target=self._serve_http, name="http", daemon=True
                ).start()
                self.server.serve_forever()
        except KeyboardInterrupt:
            log.info("收到中断，正在退出…")
        finally:
            self.close()

    def _serve_http(self) -> None:
        log.info("HTTP 监听 %s:%s", self.cfg.http_bind, self.cfg.http_port)
        try:
            http_api.serve(self.cfg, self.app)
        except Exception:  # noqa: BLE001
            log.exception("HTTP 服务异常退出")

    # ------------------------------------------------------------------ #

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        log.info("正在关闭…")
        try:
            self.hub.stop_pusher()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            game.stop_autosave()
            if game.save(self.store):
                log.info("退出前已将内存落库")
        except Exception:  # noqa: BLE001
            log.exception("退出前落库失败")
        try:
            self.store.close()
        except Exception:  # noqa: BLE001
            pass
        log.info("已关闭")


def build_service(cfg: config.Config | None = None) -> Service:
    return Service(cfg or config.load_config())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="the-door-of-bings-server",
        description="众生之门服务端（TCP 游戏协议 + HTTP 发现）",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "tcp", "http", "check", "version"],
        help="要执行的子命令（默认 serve）",
    )
    parser.add_argument("--debug", action="store_true", help="输出调试级日志")
    parser.add_argument(
        "--backend", choices=["postgres", "memory"], help="覆盖 DB_BACKEND"
    )
    args = parser.parse_args(argv)

    if args.backend:
        os.environ["DB_BACKEND"] = args.backend

    cfg = config.load_config()
    setup_logging(args.debug or cfg.debug)

    if args.command == "version":
        print(f"service  {config.SERVICE_VERSION}")
        print(f"protocol {config.PROTOCOL_VERSION}")
        return 0

    log.info("启动中：%s", cfg.describe())
    build_service(cfg).run(args.command)
    return 0


if __name__ == "__main__":
    sys.exit(main())

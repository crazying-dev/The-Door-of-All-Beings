"""HTTP 发现服务入口（纯 Flask，不含 TCP 游戏协议）。

用法::

    python main.py               # 启动 HTTP 发现服务（默认 serve）
    python main.py serve         # 同上
    python main.py check         # 自检：配置 / 端口
    python main.py version       # 打印服务与协议版本

本服务只负责两件事：

* ``/api/v1/endpoint`` 发现接口 —— 告诉客户端 TCP 游戏服务器（server-socket 组件）的地址；
* ``/healthz`` 健康检查 —— 给负载均衡 / 监控用。

真正的游戏通信全走 TCP 长连接，由独立的 **server-socket** 组件承担，
两边互不依赖，可分别部署、分别重启。
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

import config
import http_api
import lifecycle

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
    # waitress / werkzeug 的启动日志太啰嗦，降一级
    for noisy in ("waitress", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 服务装配
# ---------------------------------------------------------------------------

class Service:
    """HTTP 发现服务的装配持有者。

    ``__init__`` **不碰任何资源**，这样测试可以先构对象、再决定要不要真启。
    """

    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self.life = lifecycle.Lifecycle("http-service")

        self.app = None

        self._closed = False
        self._stop = threading.Event()
        self._signals_done = False

    # ================================================================== #
    # 初始化
    # ================================================================== #

    def s1_init(self) -> None:
        """① 环境变量写入 ② 建 Flask app ③ 注册关闭回调。"""
        self.life.enter("s1", "初始化")

        # --- ① 环境变量写入 -------------------------------------------------
        written = config.export_env(self.cfg)
        if written:
            self.life.step(f"环境变量已写入 {len(written)} 项（{', '.join(written)}）")
        else:
            self.life.step("环境变量已是规范值，无需改写")

        # --- ② 建 Flask app --------------------------------------------------
        self.app = http_api.create_app(self.cfg)

        # --- ③ 注册关闭 ------------------------------------------------------
        self.life.on_shutdown("http.shutdown", http_api.shutdown)
        self.life.step(f"HTTP 应用已构造（{self.cfg.http_bind}:{self.cfg.http_port}）")
        self.life.step(f"关闭回调栈（逆序执行）：{' <- '.join(reversed(self.life.hooks()))}")
        self.life.done()

    # ================================================================== #
    # 启动
    # ================================================================== #

    def s3_start(self) -> None:
        """后台线程起 HTTP（waitress / Werkzeug）。"""
        self.life.enter("s3", "启动")

        http_api.start_background(self.cfg, self.app)
        self.life.step(
            f"HTTP 已监听 {self.cfg.http_bind}:{self.cfg.http_port}"
        )

        self.life.done("服务已就绪")
        log.info("按 Ctrl+C 停止服务")

    # ================================================================== #
    # 关闭
    # ================================================================== #

    def s4_shutdown(self) -> int:
        """执行注册的关闭（逆序）。"""
        if self._closed:
            return 0
        self._closed = True
        self.life.enter("s4", "关闭")

        failed = self.life.shutdown()
        self.life.done(f"关闭完毕，失败 {failed} 项" if failed else "已完全关闭")
        return failed

    # ================================================================== #
    # 自检
    # ================================================================== #

    def check(self) -> bool:
        """自检：不监听端口，只确认配置正常。"""
        ok = True
        log.info("配置：%s", self.cfg.describe())
        if not self.cfg.public_host:
            log.warning("HOST 未配置：发现接口会下发空的 TCP 地址，客户端将无法连接游戏服")
        log.info("HTTP 端口可用：%s:%s", self.cfg.http_bind, self.cfg.http_port)
        return ok

    # ================================================================== #
    # 主流程
    # ================================================================== #

    def run(self, mode: str = "serve") -> int:
        """初始化 → 启动 → 阻塞等待停止信号 → 关闭。"""
        try:
            self.s1_init()

            if mode == "check":
                ok = self.check()
                return 0 if ok else 1

            self.s3_start()
            self._wait_for_stop()
            return 0

        except KeyboardInterrupt:
            log.info("收到中断（Ctrl+C），开始关闭…")
            return 0
        except SystemExit:
            raise
        except Exception:
            log.exception("启动失败")
            return 1
        finally:
            self.s4_shutdown()

    def _wait_for_stop(self) -> None:
        self._install_signals()
        self._stop.wait()

    def request_stop(self) -> None:
        """请求停止（信号处理器 / 测试脚本都调这个）。"""
        self._stop.set()

    def _install_signals(self) -> None:
        if self._signals_done:
            return
        self._signals_done = True
        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass      # 非主线程 / 不支持的平台，忽略

    def _on_signal(self, signum, _frame) -> None:
        log.info("收到信号 %s，准备关闭…", signum)
        self.request_stop()

    # ------------------------------------------------------------------ #

    def close(self) -> int:
        """向后兼容：等同于 s4。"""
        return self.s4_shutdown()


def build_service(cfg: config.Config | None = None) -> Service:
    return Service(cfg or config.load_config())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="the-door-of-bings-server",
        description="众生之门 HTTP 发现服务（TCP 游戏协议由 server-socket 组件承担）",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "check", "version"],
        help="要执行的子命令（默认 serve）",
    )
    parser.add_argument("--debug", action="store_true", help="输出调试级日志")
    args = parser.parse_args(argv)

    cfg = config.load_config()
    setup_logging(args.debug or cfg.debug)

    if args.command == "version":
        print(f"service  {config.SERVICE_VERSION}")
        print(f"protocol {config.PROTOCOL_VERSION}")
        return 0

    log.info("启动中：%s", cfg.describe())
    return build_service(cfg).run(args.command)


if __name__ == "__main__":
    sys.exit(main())

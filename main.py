"""服务端入口（TCP 游戏协议，不含 HTTP 发现接口）。

用法::

    python main.py               # 启动 TCP 游戏服务（默认）
    python main.py serve         # 同上
    python main.py tcp           # 同上（历史别名）
    python main.py check         # 自检：配置 / 存储 / 设备密钥 / 表结构
    python main.py version

启动流程（四个阶段，日志里能一行一行看到）::

    s1 初始化   1. 环境变量写入   2. 数据库游标   3. 套接字创建   4. 注册关闭
    s2 预热     1. 辅助线程（定时落库 + 防外挂）  2. 套接字绑定端口
    s3 启动     1. 启动套接字（accept + 推送）
    s4 关闭     1. 最后 game 数据落库   2. 执行注册的关闭（逆序）

HTTP 发现接口已拆到独立的 **server** 组件，两边互不依赖。
数据库不在本地：连接串来自 `.env` 的 ``DATABASE_URL``（远端 PostgreSQL），
连上时自动执行 ``schema.sql`` 建表（幂等）。
本地想不接数据库：``DB_BACKEND=memory python main.py``。
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import signal
import sys
import threading

import actions
import anticheat
import config
import device_id as devid
import game
import lifecycle
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


# ---------------------------------------------------------------------------
# 服务装配（四阶段）
# ---------------------------------------------------------------------------

class Service:
    """一整套依赖的持有者；所有装配都发生在 ``s1/s2/s3`` 里。

    ``__init__`` **不碰任何资源**（不连库、不建 socket），
    这样测试可以先构对象、再决定要不要真启。
    """

    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self.life = lifecycle.Lifecycle("service")

        self.store = None
        self.users = None
        self.hub = None
        self.server: server_mod.GameServer | None = None

        self._closed = False
        self._stop = threading.Event()
        self._signals_done = False

    # ================================================================== #
    # s1 初始化
    # ================================================================== #

    def s1_init(self) -> None:
        """① 环境变量写入 ② 数据库游标 ③ 套接字创建 ④ 注册关闭。"""
        self.life.enter("s1", "初始化")

        # --- ① 环境变量写入 -------------------------------------------------
        written = config.export_env(self.cfg)
        if written:
            self.life.step(f"环境变量已写入 {len(written)} 项（{', '.join(written)}）")
        else:
            self.life.step("环境变量已是规范值，无需改写")

        # --- ② 数据库游标 ---------------------------------------------------
        self._prepare_secret()
        self.store = store_mod.build_store(self.cfg)
        # 最早注册 ⇒ 最后执行：一定要等到服务停了、数据落完了才断库
        self.life.on_shutdown("store.close", self._close_store)
        if not self.store.ping():
            raise SystemExit(
                "数据库不可用（backend=%s）。请检查 .env 里的 DATABASE_URL：%s"
                % (self.store.backend_name, cfg_target(self.cfg))
            )
        self.life.step(f"数据库已连接：backend={self.store.backend_name}")

        self.users = UserBusiness(self.store, self.cfg.user_name_max_len)
        self.hub = SessionHub(
            max_connections=self.cfg.max_connections,
            grace_seconds=self.cfg.grace_seconds,
        )
        # 宽限期超时后的销毁动作归业务层（session.py 不认识 store / game）
        self.hub.on_grace_expired = self._on_grace_expired

        # --- ③ 套接字创建（只建不绑，绑在 s2） ------------------------------
        self.server = server_mod.GameServer(
            self.cfg, self.store, self.users, self.hub
        )
        self.server.create_socket()
        self.life.step("TCP 套接字已创建（尚未绑定端口）")
        self.life.on_shutdown("server.shutdown", self.server.shutdown)

        # --- ④ 注册关闭（已在上面逐步注册）+ 动作层注入 ----------------------
        actions.init(self.cfg, self.store, self.users, self.hub)
        self.life.step(f"已注册动作：{', '.join(sorted(actions.ACTIONS))}")
        self.life.step(f"关闭回调栈（逆序执行）：{' <- '.join(reversed(self.life.hooks()))}")
        self.life.done()

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

    # ================================================================== #
    # s2 预热
    # ================================================================== #

    def s2_warmup(self) -> None:
        """① 加载辅助线程（定时写入 & 防外挂） ② 套接字绑定端口。"""
        self.life.enter("s2", "预热")

        # --- ① 辅助线程 -----------------------------------------------------
        # 库里上一进程残留的 online 标记先清掉，否则重启后一堆“幽灵在线”。
        # 必须在 load 之前：load 会把 online 强制 False 读回内存。
        try:
            self.store.mark_all_offline()
            self.life.step("已清除数据库里残留的在线标记")
        except Exception:  # noqa: BLE001
            log.warning("清除在线标记失败（不影响启动）", exc_info=True)

        game.load_from_store(self.store)
        self.life.step(
            f"权威数据已载入内存：世界实体 {len(game.world)} 个 / 玩家 {len(game.players)} 条"
        )

        game.start_autosave(self.store, self.cfg.autosave_interval)
        self.life.on_shutdown("game.autosave", game.stop_autosave)
        self.life.step(f"定时落库线程已启动（每 {self.cfg.autosave_interval} 秒）")

        started = anticheat.start(self.store, interval=5.0)
        self.life.on_shutdown("anticheat.stop", anticheat.stop)
        self.life.step(
            "防外挂巡检已启动" if started else "防外挂：暂无规则，巡检线程未启动"
        )

        # 宽限期扫描：意外断开的会话超过 GRACE_SECONDS 才落库销毁
        self.hub.start_grace_scanner()
        self.life.on_shutdown("hub.stop_grace_scanner", self.hub.stop_grace_scanner)
        self.life.step(f"掉线宽限期已启用（{self.cfg.grace_seconds} 秒）")

        # --- ② 套接字绑定端口 -----------------------------------------------
        host, port = self.server.bind()
        self.life.step(f"TCP 已绑定 {host}:{port}（尚未 accept）")
        self.life.done()

    # ================================================================== #
    # s3 启动
    # ================================================================== #

    def s3_start(self) -> None:
        """① 启动套接字（accept）。"""
        self.life.enter("s3", "启动")

        self.server.start()
        self.life.step("TCP accept 循环已启动（v1.3：无主动推送，数据由客户端拉取）")

        self.life.done("服务已就绪")
        log.info("按 Ctrl+C 停止服务")

    # ================================================================== #
    # s4 关闭
    # ================================================================== #

    def s4_shutdown(self) -> int:
        """① 最后 game 数据落库 ② 执行注册的关闭（逆序）。"""
        if self._closed:
            return 0
        self._closed = True
        self.life.enter("s4", "关闭")

        # --- ① 最后落库 -----------------------------------------------------
        try:
            game.stop_autosave()
            anticheat.stop()
            if self.store is not None and game.save(self.store):
                self.life.step("玩家 / 世界数据已落库")
            else:
                self.life.step("落库未完成（数据库不可用，已写本地兜底快照）")
        except Exception:  # noqa: BLE001 - 关不干净也得继续关
            log.exception("关闭前落库失败")

        # --- ② 执行注册的关闭（逆序） -----------------------------------------
        failed = self.life.shutdown()
        self.life.done(f"关闭完毕，失败 {failed} 项" if failed else "已完全关闭")
        return failed

    # ------------------------------------------------------------------ #
    # 关闭回调
    # ------------------------------------------------------------------ #

    def _close_store(self) -> None:
        if self.store is not None:
            self.store.close()

    def _on_grace_expired(self, session) -> None:
        """宽限期超时（flows.md 流程 4）：落库 → 销毁 player → 作废 token。

        同账号若还有其它活跃连接（已经重连成功）就只作废 token，
        不能拆掉一个正在玩的人的数据。
        """
        user_id = session.user_id
        session.session_token = ""
        session.suspended = False

        if not user_id:
            return
        if self.hub is not None and self.hub.has_user(user_id):
            log.info("session#%s 宽限期超时，但 %s 已有新连接，保留热缓存",
                     session.sid, user_id)
            return

        game.mark_offline(user_id)
        game.save_player(user_id, self.store)
        game.destroy_player(user_id)
        # v1.3：不再广播 leave（其他客户端拉 peers 时自然感知）
        log.info("session#%s 宽限期超时：%s 已落库并销毁", session.sid, user_id)

    # ================================================================== #
    # 自检
    # ================================================================== #

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

        if self.store is not None and self.store.ping():
            log.info("存储可用：backend=%s", self.store.backend_name)
        else:
            log.error("存储不可用：backend=%s", getattr(self.store, "backend_name", "?"))
            ok = False

        # 表结构（users / player / world / world_item）能不能真读
        if self.store is not None:
            try:
                players = self.store.load_players()
                world_data = self.store.load_world()
                log.info(
                    "表结构就绪：player %d 行 / world_item %d 个 / world %s",
                    len(players),
                    len(world_data.get("items") or []),
                    "有" if world_data.get("world") else "缺",
                )
            except Exception as exc:  # noqa: BLE001
                log.error("表结构校验失败：%s", exc)
                ok = False

        log.info("已注册动作：%s", ", ".join(sorted(actions.ACTIONS)))
        return ok

    # ================================================================== #
    # 主流程
    # ================================================================== #

    def run(self, mode: str = "serve") -> int:
        """按模式跑 s1→s4（阻塞直到收到停止信号）。"""
        try:
            self.s1_init()

            if mode == "check":
                ok = self.check()
                return 0 if ok else 1

            self.s2_warmup()
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


def cfg_target(cfg: config.Config) -> str:
    """把连接串裁成“能打日志”的形状（不泄密码）。"""
    url = cfg.database_url or ""
    if "@" in url:
        return url.split("@", 1)[1]
    return url or "<未配置>"


def build_service(cfg: config.Config | None = None) -> Service:
    return Service(cfg or config.load_config())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="the-door-of-bings-server-socket",
        description="众生之门 TCP 游戏协议服务（HTTP 发现接口在独立的 server 组件）",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "tcp", "check", "version"],
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
    log.info("数据库目标：%s", cfg_target(cfg))
    return build_service(cfg).run(args.command)


if __name__ == "__main__":
    sys.exit(main())

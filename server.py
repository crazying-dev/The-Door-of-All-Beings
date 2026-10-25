"""TCP 服务端：监听 / 帧读取 / 连接线程 / 防重放。

与旧版（`my/main.py`）的关键差别：

1. **不再 ``recv(65536)`` 只读一次**。改成带缓冲的行读取器，
   切包、粘包、device_id 是 4KB 长 hex 都没问题（旧版一遇 TCP 分段就报 Malformed）。
2. **不再靠公网 IP 判断要不要启动**。TCP 永远监听，``HOST`` 只用于下发发现接口。
3. **不再 import 期请求外网**。启动失败不会因为拉不到 ipify 而直接捧异常。
4. 连接是**长连接**：同一个 socket 上可以连续收发多条请求（登录后还会收到 PUSH）。
"""

from __future__ import annotations

import logging
import socket
import threading
import time

import actions
import config
import game
import protocol
from session import Session, SessionHub

log = logging.getLogger(__name__)

## 只有会改变服务端状态的动作才做重放去重；
## ping / peers / world / state 这类幂等读无需拦，否则会误伤客户端轮询。
MUTATING_ACTIONS = {"register", "item", "device"}


# ---------------------------------------------------------------------------
# 帧读取
# ---------------------------------------------------------------------------

class FrameReader:
    """带缓冲的按行读取器。

    协议只有一个帧边界：换行 ``\\n``。一个请求 = 4 行。
    单行超长（超过 ``max_line_bytes``）直接报错，防止恶意内存耗尽。
    """

    def __init__(self, sock: socket.socket, max_line_bytes: int = 16384) -> None:
        self.sock = sock
        self.max_line_bytes = max_line_bytes
        self._buffer = bytearray()
        self._eof = False

    def _fill(self) -> bool:
        """再读一块；返回 False 表示对端已关。（socket 超时会抛 socket.timeout）"""
        if self._eof:
            return False
        chunk = self.sock.recv(8192)
        if not chunk:
            self._eof = True
            return False
        self._buffer.extend(chunk)
        return True

    def read_line(self) -> bytes | None:
        """读一行（不含换行）；EOF 返回 None。"""
        while True:
            index = self._buffer.find(b"\n")
            if index >= 0:
                line = bytes(self._buffer[:index])
                del self._buffer[: index + 1]
                if len(line) > self.max_line_bytes:
                    raise protocol.ProtocolError(
                        protocol.E_TOO_LARGE,
                        f"Line exceeds {self.max_line_bytes} bytes",
                    )
                return line.rstrip(b"\r")
            if len(self._buffer) > self.max_line_bytes:
                raise protocol.ProtocolError(
                    protocol.E_TOO_LARGE,
                    f"Line exceeds {self.max_line_bytes} bytes",
                )
            if not self._fill():
                # 对端关了：如果缓冲区里还有半行，算残缺报文
                if self._buffer:
                    raise protocol.ProtocolError(
                        protocol.E_MALFORMED, "Connection closed mid-packet"
                    )
                return None

    def read_packet(self) -> bytes | None:
        """读一个完整请求（4 行，连接符号还原）。EOF 返回 None。"""
        lines: list[bytes] = []
        for _ in range(protocol.PACKET_LINES):
            line = self.read_line()
            if line is None:
                if not lines:
                    return None
                raise protocol.ProtocolError(
                    protocol.E_MALFORMED, "Connection closed mid-packet"
                )
            lines.append(line)
        return b"\n".join(lines)


# ---------------------------------------------------------------------------
# 防重放
# ---------------------------------------------------------------------------

class ReplayGuard:
    """同一会话内、同一秒、同一正文的重复报文拦一下。

    局限：没有客户端 nonce 字段，真正的跨连接重放防护做不了（见 protocol.md TODO）。
    当前作用域 = （会话, 时间戳, 正文哈希），只能拦住脚本误双击 / 重试逻辑叠加。
    """

    def __init__(self, window: int = 300, max_entries: int = 20000) -> None:
        self.window = max(1, int(window))
        self.max_entries = max(100, int(max_entries))
        self._seen: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def seen(self, key: tuple, now: float | None = None) -> bool:
        """已见过返回 True（应拒绝）；否则登记并返回 False。"""
        now = time.time() if now is None else now
        with self._lock:
            if len(self._seen) > self.max_entries:
                self._prune(now)
            if key in self._seen:
                return True
            self._seen[key] = now + self.window
            return False

    def _prune(self, now: float) -> None:
        expired = [k for k, exp in self._seen.items() if exp < now]
        for key in expired:
            del self._seen[key]
        if len(self._seen) > self.max_entries:      # 还是太大就直接清空
            self._seen.clear()


# ---------------------------------------------------------------------------
# 服务端
# ---------------------------------------------------------------------------

class GameServer:
    """TCP 游戏协议服务端。"""

    def __init__(
        self,
        cfg: config.Config,
        store,
        users_handler,
        hub: SessionHub,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.users = users_handler
        self.hub = hub
        self.guard = ReplayGuard(window=cfg.request_time_window)

        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = time.time()
        self.stats = {"packets": 0, "errors": 0, "rejected": 0}
        self._stats_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    def bind(self) -> tuple[str, int]:
        """创建监听套接字（先 bind 再 listen，便于把真实端口告诉调用方）。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.cfg.tcp_bind, self.cfg.tcp_port))
        sock.listen(64)
        self._sock = sock
        host, port = sock.getsockname()[:2]
        log.info("TCP 服务端已监听 %s:%s", host, port)
        return host, port

    def background(self) -> None:
        """在后台线程里跑 accept 循环（测试用）。"""
        if self._sock is None:
            self.bind()
        self._thread = threading.Thread(
            target=self.serve_forever, name="tcp-accept", daemon=True
        )
        self._thread.start()

    def serve_forever(self) -> None:
        """accept 循环（阻塞）。"""
        if self._sock is None:
            self.bind()
        sock = self._sock
        assert sock is not None
        while not self._stop.is_set():
            try:
                conn, addr = sock.accept()
            except OSError:
                break          # 套接字已被 shutdown
            except KeyboardInterrupt:
                break
            peer = addr[0] if addr else "?"
            threading.Thread(
                target=self._handle_client,
                args=(conn, addr),
                name=f"client-{peer}",
                daemon=True,
            ).start()
        log.info("TCP 服务端已停止")

    def shutdown(self) -> None:
        self._stop.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self.hub.close_all()

    # ------------------------------------------------------------------ #
    # 单连接
    # ------------------------------------------------------------------ #

    def _bump(self, key: str) -> None:
        with self._stats_lock:
            self.stats[key] = self.stats.get(key, 0) + 1

    def _handle_client(self, conn: socket.socket, addr) -> None:
        """一条连接的完整生命周期：读帧 -> 解析 -> 分发 -> 回写。"""
        session = Session(conn, addr, self.hub)
        if not self.hub.add(session):
            log.warning("连接数已满，拒绝 %s", session.ip)
            try:
                conn.sendall(
                    protocol.encode_fail("-", "TOO_MANY_CONNECTIONS", "Server is busy")
                )
            except OSError:
                pass
            conn.close()
            return

        try:
            conn.settimeout(self.cfg.read_timeout)
            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            log.info("连接建立 session#%s %s", session.sid, session.ip)
            reader = FrameReader(conn, self.cfg.max_line_bytes)
            self._loop(session, reader)
        except socket.timeout:
            log.info("session#%s 读超时（%ds），断开", session.sid, self.cfg.read_timeout)
        except protocol.ProtocolError as exc:
            log.info("session#%s 协议错误：%s", session.sid, exc)
            session.send(protocol.encode_protocol_error(exc))
        except OSError as exc:
            log.info("session#%s 连接异常：%s", session.sid, exc)
        except Exception:  # noqa: BLE001 - 单连接出错不能影响其它人
            log.exception("session#%s 未预期错误", session.sid)
        finally:
            self._teardown(session)

    def _loop(self, session: Session, reader: FrameReader) -> None:
        while not self._stop.is_set() and not session.closed:
            block = reader.read_packet()
            if block is None:
                break
            self._bump("packets")
            response = self._process(block, session)
            if response and not session.send(response):
                break

    def _process(self, block: bytes, session: Session) -> bytes:
        try:
            req = protocol.parse_request(
                block, time_window=self.cfg.request_time_window
            )
        except protocol.ProtocolError as exc:
            self._bump("rejected")
            log.info("session#%s 报文被拒：%s", session.sid, exc.code)
            return protocol.encode_protocol_error(exc)

        session.touch()
        session.client_version = req.client_version or session.client_version
        session.platform = req.platform or session.platform
        session.locale = req.locale or session.locale

        if req.action in MUTATING_ACTIONS:
            key = (session.sid, req.ts, req.body_hash)
            if self.guard.seen(key):
                self._bump("rejected")
                return protocol.encode_fail(
                    req.action, protocol.E_DUPLICATE, "同一报文已处理过"
                )

        return actions.dispatch(req, session)

    def _teardown(self, session: Session) -> None:
        was_authed = session.authenticated
        user_id, user_name = session.user_id, session.user_name
        session.close()
        self.hub.remove(session)
        if was_authed and not self.hub.has_user(user_id):
            game.mark_offline(user_id)
            self.hub.push_event("leave", user_id, user_name)
        log.info(
            "连接断开 session#%s %s（在线会话 %d）",
            session.sid,
            user_id or "anonymous",
            self.hub.count(),
        )

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        """给 /healthz 用的运行时快照。"""
        with self._stats_lock:
            stats = dict(self.stats)
        return {
            "uptime": round(time.time() - self._started_at, 1),
            "connections": self.hub.count(),
            "online_users": self.hub.user_count(),
            "sessions_dropped": self.hub.dropped(),
            "packets": stats.get("packets", 0),
            "rejected": stats.get("rejected", 0),
            "online_players": game.online_count(),
        }

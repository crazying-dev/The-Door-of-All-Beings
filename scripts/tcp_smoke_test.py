"""TCP 协议冒烟测试（零依赖，不需要数据库 / 不需要 pytest）。

用法::

    uv run scripts/tcp_smoke_test.py
    uv run scripts/tcp_smoke_test.py -v      # 打印每条用例

它做的事：

1. 用 ``DB_BACKEND=memory`` 拉起一整套 Service（TCP 监听 127.0.0.1:0 随机端口）；
2. 用原始 socket + `protocol.build_request/parse_response` 说完整链路；
3. 覆盖：会话 Token 下发与校验 / 握手 / 设备签发与确认 / 名称查重 / 注册 / 登录
   （含 file_hash）/ 重放 / 哈希篡改 / 时间戳过期 / 未知动作 / 设备伪造 /
   10Hz PUSH / 双客户端同屏 / 正常下线完整拆除 / 意外断开宽限期与超时销毁。

退出码 0 = 全绿。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import socket
import sys
import threading
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Windows 控制台默认 GBK，→ / ✓ 这类字符会直接抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# 必须在 import config 之前，否则 .env 里的 DB_BACKEND=postgres 会盖掉它
os.environ["DB_BACKEND"] = "memory"

import config  # noqa: E402
import game  # noqa: E402
import protocol  # noqa: E402

USER_ID_RE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")


# ---------------------------------------------------------------------------
# 测试框架（极小）
# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.passed = 0
        self.failed: list[tuple[str, str]] = []
        self._current = ""

    def case(self, name: str) -> None:
        self._current = name
        if self.verbose:
            print(f"\n[case] {name}")

    def check(self, cond: bool, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            if self.verbose:
                print(f"  ✓ {detail or self._current}")
            return True
        self.failed.append((self._current, detail or self._current))
        print(f"  ✗ {self._current} :: {detail}")
        return False

    def eq(self, actual, expected, detail: str = "") -> bool:
        return self.check(
            actual == expected,
            f"{detail} expect={expected!r} got={actual!r}",
        )

    def summary(self) -> int:
        total = self.passed + len(self.failed)
        print(f"\n{'=' * 60}")
        print(f"PASSED {self.passed}/{total}    FAILED {len(self.failed)}")
        for case, detail in self.failed:
            print(f"  ✗ {case} :: {detail}")
        print("=" * 60)
        return 0 if not self.failed else 1


# ---------------------------------------------------------------------------
# TCP 客户端
# ---------------------------------------------------------------------------
class Client:
    """极简客户端：一次请求 / 一条响应，PUSH 帧单独收集。

    v1.2：建连后服务端会主动推一帧 ``PUSH | token``，构造时默认先取到它，
    后续请求自动携带（想测旧行为可用 ``await_token=False``）。
    """

    def __init__(self, host: str, port: int, await_token: bool = True) -> None:
        self.sock = socket.create_connection((host, port), timeout=5.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._buf = b""
        self.pushes: list[protocol.Response] = []
        self.device_id = ""
        self.user_id = ""
        self.token = ""
        self.token_expire = 0
        if await_token:
            self.await_token()

    # -- Token -------------------------------------------------------
    def await_token(self, timeout: float = 5.0) -> protocol.Response | None:
        """读服务端建连时主动推的 ``PUSH | token`` 帧。"""
        deadline = time.time() + timeout
        while True:
            line = self.read_line(max(0.05, deadline - time.time()))
            if line is None:
                return None
            resp = protocol.parse_response(line)
            if resp.kind == "PUSH" and resp.topic == protocol.TOPIC_TOKEN:
                self.token = str(resp.data.get("token") or "")
                self.token_expire = int(resp.data.get("expire") or 0)
                self.pushes.append(resp)      # 留给用例回看「确实推过这一帧」
                return resp
            if resp.kind == "PUSH":
                self.pushes.append(resp)

    # -- 低层 ---------------------------------------------------------
    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read_line(self, timeout: float = 5.0) -> str | None:
        deadline = time.time() + timeout
        while True:
            index = self._buf.find(b"\n")
            if index >= 0:
                line = self._buf[:index]
                self._buf = self._buf[index + 1 :]
                return line.decode("utf-8", "replace")
            left = deadline - time.time()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except (socket.timeout, TimeoutError):
                return None
            if not chunk:
                return None
            self._buf += chunk

    def read_frame(self, timeout: float = 5.0) -> protocol.Response | None:
        """读下一条 **非 PUSH** 帧；PUSH 存进 self.pushes。"""
        while True:
            line = self.read_line(timeout)
            if line is None:
                return None
            resp = protocol.parse_response(line)
            if resp.kind == "PUSH":
                self.pushes.append(resp)
                continue
            return resp

    # -- 高层 ---------------------------------------------------------
    def call(
        self,
        action: str,
        data: str = "",
        *,
        device: str | None = None,
        user: str | None = None,
        raw_device_id: str | None = None,
        session_token: str | None = None,
        **kw,
    ) -> protocol.Response | None:
        """发一条请求并等响应。device/user/token 默认沿用本会话已记住的值。"""
        dev = self.device_id if device is None else device
        usr = self.user_id if user is None else user
        tok = self.token if session_token is None else session_token
        if raw_device_id is not None:
            dev = raw_device_id
        packet = protocol.build_request(action, data, dev, usr, session_token=tok, **kw)
        self.send_raw(packet)
        resp = self.read_frame()
        return resp

    def drain_pushes(self, seconds: float = 0.5) -> list[protocol.Response]:
        """在 seconds 内尽量多收 PUSH 帧。"""
        self.pushes.clear()
        deadline = time.time() + seconds
        while time.time() < deadline:
            line = self.read_line(max(0.05, deadline - time.time()))
            if line is None:
                break
            resp = protocol.parse_response(line)
            if resp.kind == "PUSH":
                self.pushes.append(resp)
        return self.pushes

    def find_push(self, topic: str) -> protocol.Response | None:
        """在已收到的 PUSH 里找指定 topic 的最后一帧。"""
        for resp in reversed(self.pushes):
            if resp.topic == topic:
                return resp
        return None

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(verbose: bool = False) -> int:
    import dataclasses

    import main as main_mod

    t = Runner(verbose)

    cfg = dataclasses.replace(
        config.load_config(),
        db_backend="memory",
        tcp_bind="127.0.0.1",
        tcp_port=0,
        autosave_interval=3600,
        push_hz=10,
        grace_seconds=2,      # 宽限期缩到 2s，超时销毁的用例才能跑得快
        device_id_secret="",  # 内存后端允许临时密钥
    )
    service = main_mod.Service(cfg)
    # 四阶段启动（与 python main.py serve 完全同一条路径）
    service.s1_init()
    service.s2_warmup()
    service.s3_start()
    host, port = service.server.bind()  # 幂等，只为拿到真实端口（cfg 里是 0）
    print(f"服务端已启动 tcp://{host}:{port}（backend=memory）")

    a = Client(host, port)
    b = Client(host, port)
    extra: list[Client] = []      # 中途新开的连接，统一在 finally 里关
    try:
        # ------------------------------------------------------------------
        t.case("v1.2 建连即推送会话 Token")
        t.check(len(a.token) == 32, f"token 应为 32 位 hex，实得 {a.token!r}")
        t.eq(a.token_expire, 180, "token 有效期（秒）")
        t.check(a.token != b.token, "两条连接的 token 应不同")
        first_push = a.find_push(protocol.TOPIC_TOKEN)
        t.check(first_push is not None, "应收到 PUSH | token 帧")

        t.case("带错 token 的请求被拒 + 回推 reset_token")
        a.pushes.clear()
        r = a.call("ping", session_token="deadbeef" + a.token[8:])
        t.check(r is not None and r.kind == "FAIL", "应返回 FAIL")
        if r is not None:
            t.eq(r.code, protocol.E_TOKEN_EXPIRED, "错误码")
        t.check(a.find_push(protocol.TOPIC_RESET_TOKEN) is not None,
                "应同时收到 PUSH | reset_token")

        t.case("hello 握手")
        r = a.call("hello")
        t.check(r is not None, "没有响应")
        if r is not None:
            t.eq(r.kind, "OK", "frame kind")
            t.eq(r.data.get("protocol_version"), protocol.CURRENT_VERSION, "协议版本")
            actions = r.data.get("actions") or []
            for wanted in ("device", "check_name", "register", "login", "logout",
                           "state", "peers", "world", "item"):
                t.check(wanted in actions, f"动作表包含 {wanted}")

        t.case("协议级错误：坏 magic")
        a.send_raw(b"EvilMagic | 1.1 | 0 | none\nx | \ny | \nz | z\n")
        r = a.read_frame()
        t.check(r is not None and r.kind == "FAIL", "应返回 FAIL")
        if r is not None:
            t.eq(r.code, protocol.E_BAD_MAGIC, "错误码")

        t.case("协议级错误：明文哈希不对")
        bad = protocol.build_request("ping").replace(b"\n", b"\n", 1)
        lines = bad.split(b"\n")
        lines[3] = b"0" * 64 + b" | " + b"0" * 64
        a.send_raw(b"\n".join(lines))
        r = a.read_frame()
        t.check(r is not None and r.kind == "FAIL", "应返回 FAIL")
        if r is not None:
            t.eq(r.code, protocol.E_BAD_PLAIN_HASH, "错误码")

        t.case("协议级错误：时间戳过期")
        a.send_raw(protocol.build_request("ping", ts=int(time.time()) - 10000))
        r = a.read_frame()
        t.check(r is not None and r.kind == "FAIL", "应返回 FAIL")
        if r is not None:
            t.eq(r.code, protocol.E_REQUEST_EXPIRED, "错误码")

        t.case("ping 心跳")
        r = a.call("ping", device="", user="")
        t.check(r is not None and r.ok, "ping 应 OK")

        # ------------------------------------------------------------------
        t.case("device 签发新设备 ID")
        r = a.call("device", "", device="", user="")
        t.check(r is not None and r.ok, "应 OK")
        if r is not None and r.ok:
            dev = r.data.get("device_id", "")
            t.eq(len(dev), 2048, "1024 字节 → 2048 hex 字符")
            t.eq(r.data.get("known"), False, "known 应为 False")
            t.eq(r.data.get("account"), None, "新设备没有账号")
            a.device_id = dev

        t.case("device 确认既有设备（不重新签发）")
        r = a.call("device")
        t.check(r is not None and r.ok, "应 OK")
        if r is not None and r.ok:
            t.eq(r.data.get("device_id"), a.device_id, "应原样返回同一个 ID")
            t.eq(r.data.get("known"), True, "known 应为 True")
            t.eq(r.data.get("account"), None, "还没注册，account 应为 null")

        t.case("设备伪造被拒")
        tampered = bytearray(bytes.fromhex(a.device_id))
        tampered[-1] ^= 0xFF
        r = a.call("whoami", raw_device_id=tampered.hex(), user="")
        t.check(r is not None and r.kind == "FAIL", "应返回 FAIL")
        if r is not None:
            t.eq(r.code, protocol.E_BAD_DEVICE_ID, "错误码")

        t.case("未登录调 state 被拒")
        r = a.call("state", "0&0&0&0")
        t.check(r is not None and r.kind == "FAIL", "应返回 FAIL")
        if r is not None:
            t.eq(r.code, protocol.E_NOT_AUTHENTICATED, "错误码")

        # ------------------------------------------------------------------
        t.case("check_name 重名检测")
        r = a.call("check_name", "勇者")
        t.check(r is not None and r.ok, "应 OK")
        if r is not None and r.ok:
            t.eq(r.data.get("available"), True, "还未注册，应用")

        t.case("register 注册（带客户端 player_id UUID）")
        r = a.call("register", "勇者&human&11111111-2222-3333-4444-555555555555")
        t.check(r is not None and r.ok, "应 OK")
        if r is not None and r.ok:
            t.eq(r.data.get("user_name"), "勇者", "用户名")
            t.eq(r.data.get("player_id"), "11111111-2222-3333-4444-555555555555",
                 "回显客户端生成的 player_id")
            uid = r.data.get("user_id", "")
            t.check(bool(USER_ID_RE.match(uid)), f"user_id 格式 {uid!r}")
            a.user_id = uid

        t.case("check_name 注册后变重名")
        r = a.call("check_name", "勇者")
        t.check(r is not None and r.ok, "应 OK")
        if r is not None and r.ok:
            t.eq(r.data.get("available"), False, "已注册应不可用")
            t.eq(r.data.get("reason"), "taken", "不可用原因")
        r = a.call("check_name", "bad&name")
        t.check(r is not None and r.ok, "应 OK")
        if r is not None and r.ok:
            t.eq(r.data.get("reason"), "invalid", "非法字符算 invalid")

        t.case("register 时设备才入库")
        r = a.call("device")
        t.check(r is not None and r.ok, "device 应 OK")
        if r is not None and r.ok:
            t.eq((r.data or {}).get("stored"), True, "register 后设备已在库里")

        t.case("重放同一报文被去重")
        packet = protocol.build_request("register", "另一个名字", a.device_id, "")
        a.send_raw(packet)
        r1 = a.read_frame()
        a.send_raw(packet)
        r2 = a.read_frame()
        t.check(r2 is not None and r2.kind == "FAIL", "第二条应被拒")
        if r2 is not None:
            t.eq(r2.code, protocol.E_DUPLICATE, "错误码")
        t.check(r1 is not None, "第一条应有响应")

        t.case("whoami 已认证")
        r = a.call("whoami")
        t.check(r is not None and r.ok, "应 OK")
        if r is not None and r.ok:
            t.eq(r.data.get("authenticated"), True, "authenticated")
            t.eq(r.data.get("user_id"), a.user_id, "user_id")
            t.eq(r.data.get("device_id_len"), 1024, "设备 ID 字节数")

        t.case("state 上报位置")
        r = a.call("state", "12.5&0&-8&90&80&moving")
        t.check(r is not None and r.ok, "应 OK")

        t.case("peers 看到自己")
        r = a.call("peers")
        t.check(r is not None and r.ok, "应 OK")
        if r is not None and r.ok:
            players = r.data.get("players") or []
            me = [p for p in players if p.get("user_id") == a.user_id]
            t.eq(len(me), 1, "在线列表里的自己")
            if me:
                t.eq(me[0].get("pos_x"), 12.5, "pos_x")
                t.eq(me[0].get("state"), "moving", "state")

        t.case("item 与世界快照")
        r = a.call("item", "door_01&door&0&0&-8&90&idle")
        t.check(r is not None and r.ok, "item 应 OK")
        r = a.call("world")
        t.check(r is not None and r.ok, "world 应 OK")
        if r is not None and r.ok:
            items = r.data.get("items") or []
            t.eq(len(items), 1, "世界物品数")
            if items:
                t.eq(items[0].get("id"), "door_01", "物品 id")

        t.case("10Hz PUSH 推送")
        pushes = [p for p in a.drain_pushes(0.6) if p.topic == protocol.TOPIC_WORLD]
        t.check(len(pushes) >= 2, f"0.6s 内应收到多条 world 推送，实际 {len(pushes)}")
        if pushes:
            payload = pushes[-1].data
            t.check(isinstance(payload.get("players"), list), "payload 带 players")
            t.check(payload.get("seq", 0) > 0, "payload 带递增 seq")
            t.check(any(p.get("user_id") == a.user_id for p in payload["players"]), "推送里包含自己")

        # ------------------------------------------------------------------
        t.case("第二个客户端：重名被拒 + 同屏")
        r = b.call("device", "")
        b.device_id = (r.data or {}).get("device_id", "") if r is not None else ""
        t.check(len(b.device_id) == 2048, "B 也拿到设备 ID")
        r = b.call("register", "勇者")
        t.check(r is not None and r.kind == "FAIL", "重名应失败")
        if r is not None:
            t.eq(r.code, protocol.E_NAME_TAKEN, "错误码")
        r = b.call("register", "法师")
        t.check(r is not None and r.ok, "改名后应成功")
        if r is not None and r.ok:
            b.user_id = r.data.get("user_id", "")
        r = b.call("peers")
        ids = {p.get("user_id") for p in ((r.data or {}).get("players") or [])} if r else set()
        t.check(a.user_id in ids and b.user_id in ids, f"双方应互相可见 ids={ids}")

        t.case("未知动作")
        r = a.call("dance", "")
        t.check(r is not None and r.kind == "FAIL", "应失败")
        if r is not None:
            t.eq(r.code, protocol.E_UNKNOWN_ACTION, "错误码")

        t.case("带 user_id 但设备不匹配")
        r = a.call("peers", user=b.user_id)
        t.check(r is not None and r.kind == "FAIL", "应失败")
        if r is not None:
            t.eq(r.code, protocol.E_BAD_USER_ID, "错误码")

        # ------------------------------------------------------------------
        t.case("logout 完整拆除（流程 3）")
        b_user = b.user_id
        r = b.call("logout")
        t.check(r is not None and r.ok, "logout 应 OK")
        if r is not None:
            t.eq(r.data.get("saved"), True, "已落库")
            t.eq(r.data.get("authenticated"), False, "已解除认证")
        t.check(game.get_player(b_user) is None, "player 热缓存已销毁")
        b.pushes.clear()
        r = b.call("state", "0&0&0&0")          # 拿已作废的 token 再来
        t.check(r is not None and r.kind == "FAIL", "旧 token 应被拒")
        if r is not None:
            t.eq(r.code, protocol.E_TOKEN_EXPIRED, "错误码")
        t.check(b.find_push(protocol.TOPIC_RESET_TOKEN) is not None,
                "应同时收到 reset_token 推送")
        t.case("device 确认后能拿回账号（免登录入口）")
        r = a.call("device")
        account = (r.data or {}).get("account") if r is not None else None
        t.check(isinstance(account, dict), f"account 应为对象 got={account!r} resp={r!r}")
        if isinstance(account, dict):
            t.eq(account.get("user_id"), a.user_id, "account.user_id")
            t.eq(account.get("user_name"), "勇者", "account.user_name")

        t.case("下线后重连：login 带 file_hash，player 从库重载")
        b2 = Client(host, port)
        extra.append(b2)
        b2.device_id = b.device_id
        r = b2.call(
            "login", '{"player_id":"%s","file_hash":"hash-1"}' % b_user, user=""
        )
        t.check(r is not None and r.ok, f"login 应 OK got={r}")
        if r is not None and r.ok:
            t.eq(r.data.get("user_id"), b_user, "user_id")
            t.eq(r.data.get("session_token"), b2.token, "响应回显会话 token")
        r = b2.call("peers")
        ids = {p.get("user_id") for p in ((r.data or {}).get("players") or [])} if r else set()
        t.check(b_user in ids, f"重载后应重新在线 ids={ids}")

        t.case("file_hash 不一致时拒登（流程 2 完整性校验）")
        b3 = Client(host, port)
        extra.append(b3)
        b3.device_id = b.device_id
        r = b3.call(
            "login", '{"player_id":"%s","file_hash":"hash-2"}' % b_user, user=""
        )
        t.check(r is not None and r.kind == "FAIL", "哈希不一致应失败")
        if r is not None:
            t.eq(r.code, protocol.E_FILE_HASH_MISMATCH, "错误码")
        b3.close()

        # ------------------------------------------------------------------
        t.case("意外断开：宽限期内仍在线")
        r = b2.call("state", "77&0&0&0")
        t.check(r is not None and r.ok, "上报位置应 OK")
        b2.sock.close()                       # 硬断，不发 logout
        time.sleep(0.4)
        t.check(game.get_player(b_user) is not None, "宽限期内热缓存仍在")
        row = game.get_player(b_user) or {}
        t.eq(row.get("online"), True, "宽限期内仍算在线")
        r = a.call("peers")
        ids = {p.get("user_id") for p in ((r.data or {}).get("players") or [])} if r else set()
        t.check(b_user in ids, f"其他玩家此时仍能看到他 ids={ids}")

        t.case("宽限期内重连：同设备接管，位置不丢")
        b4 = Client(host, port)
        extra.append(b4)
        b4.device_id = b.device_id
        r = b4.call(
            "login", '{"player_id":"%s","file_hash":"hash-1"}' % b_user, user=""
        )
        t.check(r is not None and r.ok, f"重连登录应 OK got={r}")
        row = game.get_player(b_user) or {}
        t.eq(row.get("pos_x"), 77.0, "宽限期内重连保留实时位置")
        t.eq(len(service.hub._suspended), 0, "挂起会话已被接管")

        t.case("宽限期超时：落库 + 销毁 + 广播 leave")
        b4.sock.close()
        pushes = a.drain_pushes(cfg.grace_seconds + 1.5)
        leaves = [
            p for p in pushes
            if p.topic == protocol.TOPIC_EVENT and (p.data or {}).get("kind") == "leave"
            and (p.data or {}).get("user_id") == b_user
        ]
        t.check(bool(leaves), "应收到该玩家的 leave 广播")
        t.check(game.get_player(b_user) is None, "宽限期超时后 player 已销毁")
        r = a.call("peers")
        ids = {p.get("user_id") for p in ((r.data or {}).get("players") or [])} if r else set()
        t.check(b_user not in ids, f"超时后应离线 ids={ids}")
        t.check(a.user_id in ids, "A 仍在线")

        t.case("运行统计")
        stats = service.server.snapshot()
        t.check(stats["packets"] > 0, f"packets={stats['packets']}")
        t.check(stats["rejected"] > 0, f"rejected={stats['rejected']}")
        t.check(stats["online_players"] >= 1, f"online_players={stats['online_players']}")

    except Exception:  # noqa: BLE001 - 测试脚本把异常当失败，不要吃掉
        t.failed.append(("<exception>", traceback.format_exc()))
        print(traceback.format_exc())
    finally:
        for client in [a, b, *extra]:
            client.close()
        service.close()

    return t.summary()


def main() -> int:
    parser = argparse.ArgumentParser(description="TCP 协议冒烟测试")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每条断言")
    args = parser.parse_args()
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s | %(message)s")
    return run(args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())

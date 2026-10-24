"""世界通道冒烟测试（零第三方依赖）。

它自己实现了 RFC6455 客户端（握手 + 帧编码/解码），不依赖 websocket-client，
所以不靠「自己写的服务端配自己写的库」蒙混过关，而是按协议字节逐帧验证。

用法::

    # 服务端跑在默认地址，且 WORLD_REQUIRE_AUTH=0
    uv run scripts/ws_smoke_test.py

    # 指定地址 / 带上账号（WORLD_REQUIRE_AUTH=1 时必须提供）
    WS_PORT=2690 WS_DEVICE_ID=xxx WS_USER_ID=ZWME-2WKQ WS_USER_NAME=某位旅人 \\
        uv run scripts/ws_smoke_test.py

退出码 0 = 全部通过，1 = 有失败。
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import struct
import sys
import time

HOST = os.environ.get("WS_HOST", "127.0.0.1")
PORT = int(os.environ.get("WS_PORT", "2690"))
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    RESULTS.append((name, bool(ok)))


class Client:
    """最小可用的 WebSocket 客户端（只支持文本帧 + 关闭 + ping）。"""

    def __init__(self, path: str = "/ws/world") -> None:
        self.sock = socket.create_connection((HOST, PORT), timeout=10)
        self.key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {HOST}:{PORT}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {self.key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("握手期间连接被关闭")
            buffer += chunk
        head, rest = buffer.split(b"\r\n\r\n", 1)
        self.status_line = head.split(b"\r\n")[0].decode()
        headers: dict[str, str] = {}
        for line in head.split(b"\r\n")[1:]:
            if b":" in line:
                name, value = line.split(b":", 1)
                headers[name.strip().lower().decode()] = value.strip().decode()
        expected = base64.b64encode(hashlib.sha1((self.key + GUID).encode()).digest()).decode()
        self.accept_ok = headers.get("sec-websocket-accept", "") == expected
        self.buf = rest

    def send_text(self, text: str) -> None:
        data = text.encode()
        header = bytearray([0x81])  # FIN + text
        mask = os.urandom(4)
        size = len(data)
        if size < 126:
            header.append(0x80 | size)
        elif size < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", size)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def send_json(self, payload: dict) -> None:
        self.send_text(json.dumps(payload, ensure_ascii=False))

    def _need(self, count: int) -> None:
        while len(self.buf) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("连接已关闭")
            self.buf += chunk

    def frame(self) -> tuple[int, bytes]:
        self._need(2)
        b0, b1 = self.buf[0], self.buf[1]
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        size = b1 & 0x7F
        offset = 2
        if size == 126:
            self._need(offset + 2)
            size = struct.unpack(">H", self.buf[offset : offset + 2])[0]
            offset += 2
        elif size == 127:
            self._need(offset + 8)
            size = struct.unpack(">Q", self.buf[offset : offset + 8])[0]
            offset += 8
        mask = b""
        if masked:
            self._need(offset + 4)
            mask = self.buf[offset : offset + 4]
            offset += 4
        self._need(offset + size)
        payload = self.buf[offset : offset + size]
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.buf = self.buf[offset + size :]
        return opcode, payload

    def wait_json(self, types: set[str], timeout: float = 5.0) -> dict:
        deadline = time.time() + timeout
        seen: list[str] = []
        while time.time() < deadline:
            opcode, payload = self.frame()
            if opcode == 8:
                raise RuntimeError("服务端主动关闭连接")
            if opcode == 9:  # ping 帧不关心
                continue
            message = json.loads(payload.decode())
            seen.append(str(message.get("type")))
            if message.get("type") in types:
                return message
        raise RuntimeError(f"超时未收到 {types}，已收到 {seen}")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def http_state() -> tuple[int, dict]:
    # 不用 urllib.request：它会读 Windows 注册表里的系统代理，把 127.0.0.1 也转发出去
    conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
    conn.request("GET", "/api/v1/world/state")
    resp = conn.getresponse()
    body = json.loads(resp.read().decode())
    status = resp.status
    conn.close()
    return status, body


def main() -> int:
    device_id = os.environ.get("WS_DEVICE_ID", "")
    user_id = os.environ.get("WS_USER_ID", "SMOKE-A")
    user_name = os.environ.get("WS_USER_NAME", "冒烟测试")

    hello = {"type": "hello", "user_id": user_id, "user_name": user_name, "client_version": "0.1.0", "state": "lobby"}
    if device_id:
        hello["device_id"] = device_id

    a = Client()
    check("handshake_101", a.status_line.endswith("101 Switching Protocols"))
    check("accept_key", a.accept_ok)

    a.send_json(hello)
    try:
        welcome = a.wait_json({"welcome", "error"})
    except Exception as exc:  # noqa: BLE001
        print("握手失败：", exc)
        return 1
    if welcome.get("type") == "error":
        print(f"服务端拒绝握手：{welcome.get('code')} {welcome.get('msg')}")
        print("（WORLD_REQUIRE_AUTH=1 时请通过 WS_DEVICE_ID / WS_USER_ID / WS_USER_NAME 提供真实账号）")
        return 1

    check("welcome_you", welcome.get("you") == user_id)
    check("welcome_tick_hz", welcome.get("tick_hz", 0) >= 1)

    first = a.wait_json({"world"})
    check("world_list_present", isinstance(first.get("world"), list))
    check("has_self_in_players", any(p.get("user_id") == user_id for p in first.get("players", [])))

    a.send_json(
        {
            "type": "player.update",
            "pos": [1.5, 2.25, -3.0],
            "yaw": 91.5,
            "action": "walk",
            "state": "in_game",
        }
    )
    pushed = a.wait_json({"world"})
    me = next((p for p in pushed.get("players", []) if p.get("user_id") == user_id), {})
    check("pos_synced", me.get("pos") == [1.5, 2.25, -3.0])
    check("yaw_synced", me.get("yaw") == 91.5)
    check("action_synced", me.get("action") == "walk")
    check("state_synced", me.get("state") == "in_game")

    # 非法字段只回 error，不能断线
    a.send_json({"type": "player.update", "pos": [1, 2], "yaw": 0})
    check("bad_field_returns_error", a.wait_json({"error"}).get("code") == "BAD_FIELD")
    a.send_json({"type": "ping"})
    check("still_alive_after_error", a.wait_json({"pong"}).get("type") == "pong")

    status, body = http_state()
    check("http_state_200", status == 200)
    check("http_state_has_two_lists", isinstance(body.get("players"), list) and isinstance(body.get("world"), list))
    check("http_state_matches_ws", any(p.get("user_id") == user_id for p in body.get("players", [])))

    a.close()
    time.sleep(0.8)
    _, body = http_state()
    check("player_removed_after_close", not any(p.get("user_id") == user_id for p in body.get("players", [])))

    failed = [name for name, ok in RESULTS if not ok]
    for name, ok in RESULTS:
        print(("PASS " if ok else "FAIL ") + name)
    print(f"TOTAL {len(RESULTS)}  FAILED {len(failed)}")
    for name in failed:
        print("  ->", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

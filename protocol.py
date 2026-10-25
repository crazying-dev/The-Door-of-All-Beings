"""套接字协议（v1.1）：报文解析 / 响应构造 / 错误码。

完整规范见 protocol.md。举要：

请求（4 行，行间 ``\\n``，整体以 ``\\n`` 结尾作为帧边界）::

    TheDoorOfBings | 1.1 | <ts> | <encryption> | <client_version> | <platform> | <locale>
    <action> | <action_data>
    <device_id_hex> | <user_id>
    <plain_hash> | <cipher_hash>

响应 / 推送（单行）::

    OK   | <action> | <json>
    FAIL | <action> | <code> | <message>
    PUSH | <topic>  | <json>

所有协议级错误也统一走 ``FAIL | - | <code> | <message>``，
不再像 v1.0 那样返回裸文本（裸文本客户端无法可靠区分成功与失败）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------
MAGIC = "TheDoorOfBings"
## 接受的历史版本。1.0 报文头只有 4 个字段，1.1 追加了客户端信息。
SUPPORTED_VERSIONS = ("1.0", "1.1")
CURRENT_VERSION = "1.1"
## 当前只支持不加密；引入加密后把新算法加进来即可
ENCRYPTION_METHODS = ("none",)

## 请求报文共几行
PACKET_LINES = 4
## 推送帧的 topic
TOPIC_WORLD = "world"
TOPIC_EVENT = "event"

# ---------------------------------------------------------------------------
# 错误码（机器可读，客户端据此分支；人读说明另走 message）
# ---------------------------------------------------------------------------
E_BAD_MAGIC = "BAD_MAGIC"
E_BAD_VERSION = "BAD_VERSION"
E_REQUEST_EXPIRED = "REQUEST_EXPIRED"
E_DUPLICATE = "DUPLICATE_REQUEST"
E_BAD_ENCRYPTION = "BAD_ENCRYPTION"
E_BAD_PLAIN_HASH = "BAD_PLAIN_HASH"
E_BAD_CIPHER_HASH = "BAD_CIPHER_HASH"
E_MALFORMED = "MALFORMED"
E_TOO_LARGE = "TOO_LARGE"
E_BAD_DEVICE_ID = "BAD_DEVICE_ID"
E_BAD_USER_ID = "BAD_USER_ID"
E_UNKNOWN_ACTION = "UNKNOWN_ACTION"
E_BAD_PAYLOAD = "BAD_PAYLOAD"
E_NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
E_NOT_READY = "NOT_READY"
E_NAME_TAKEN = "NAME_TAKEN"
E_USER_NOT_FOUND = "USER_NOT_FOUND"
E_DEVICE_MISMATCH = "DEVICE_MISMATCH"
E_DEVICE_ALREADY_BOUND = "DEVICE_ALREADY_BOUND"
E_ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
E_ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
E_INTERNAL = "INTERNAL_ERROR"
E_SHUTTING_DOWN = "SHUTTING_DOWN"

# 错误码 -> 默认人读说明
ERROR_MESSAGES = {
    E_BAD_MAGIC: "Invalid protocol magic header",
    E_BAD_VERSION: "Unsupported protocol version",
    E_REQUEST_EXPIRED: "Request expired",
    E_DUPLICATE: "Duplicate request",
    E_BAD_ENCRYPTION: "Unsupported encryption algorithm",
    E_BAD_PLAIN_HASH: "Invalid plain hash",
    E_BAD_CIPHER_HASH: "Invalid cipher hash",
    E_MALFORMED: "Malformed packet structure",
    E_TOO_LARGE: "Packet line too large",
    E_BAD_DEVICE_ID: "Invalid Device ID",
    E_BAD_USER_ID: "Invalid User ID",
    E_UNKNOWN_ACTION: "Unknown action",
    E_BAD_PAYLOAD: "Malformed payload",
    E_NOT_AUTHENTICATED: "Not authenticated",
    E_NOT_READY: "Service not ready",
    E_NAME_TAKEN: "User name already exists",
    E_USER_NOT_FOUND: "User not found or device mismatch",
    E_DEVICE_MISMATCH: "Device mismatch",
    E_DEVICE_ALREADY_BOUND: "Device already bound to another account",
    E_ACCOUNT_LOCKED: "Account is temporarily locked",
    E_ACCOUNT_DISABLED: "Account is not active",
    E_INTERNAL: "Internal server error",
    E_SHUTTING_DOWN: "Server is shutting down",
}


class ProtocolError(Exception):
    """协议级错误：直接回 FAIL | - | code | message。"""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, code)
        super().__init__(f"{self.code}: {self.message}")


# ---------------------------------------------------------------------------
# 请求对象
# ---------------------------------------------------------------------------

@dataclass
class Request:
    """一条解析完毕且已通过密码学校验的请求。"""

    version: str
    ts: int
    encryption: str
    client_version: str
    platform: str
    locale: str
    action: str
    action_data: str
    device_id_hex: str
    user_id: str
    plain_hash: str
    cipher_hash: str
    ## 正文两行原文（用于防重放指纹，不含行尾换行）
    body_raw: bytes = b""
    ## 正文 sha256 hexdigest
    body_hash: str = ""

    @property
    def device_id(self) -> bytes:
        """device_id 的原始字节（hex 非法时返回 b""）。"""
        try:
            return bytes.fromhex(self.device_id_hex) if self.device_id_hex else b""
        except ValueError:
            return b""

    @property
    def has_device(self) -> bool:
        return self.device_id_hex != ""


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(E_MALFORMED, f"Packet is not valid UTF-8: {exc}") from exc


def parse_request(
    block: bytes,
    *,
    now: float | None = None,
    time_window: int = 300,
) -> Request:
    """把 4 行报文（不含结尾换行）解析为 Request。

    哈希对“客户端将发出的正文两行原文”逐字节计算：
    ``sha256(line2 + b"\\n" + line3)``，不做 strip。
    （旧版先 strip 再算，客户端只要多打一个空格就永远合不上。）
    """
    if not block:
        raise ProtocolError(E_MALFORMED, "Empty packet")

    lines = block.split(b"\n")
    if len(lines) != PACKET_LINES:
        raise ProtocolError(
            E_MALFORMED,
            f"Expect {PACKET_LINES} lines, got {len(lines)}",
        )

    # --- 1. 请求头 -------------------------------------------------------
    head = [p.strip() for p in _decode(lines[0]).split("|")]
    if len(head) < 4:
        raise ProtocolError(E_MALFORMED, "Header needs at least 4 fields")
    if head[0] != MAGIC:
        raise ProtocolError(E_BAD_MAGIC)
    version = head[1]
    if version not in SUPPORTED_VERSIONS:
        raise ProtocolError(E_BAD_VERSION, f"Unsupported protocol version {version!r}")
    try:
        ts = int(head[2])
    except ValueError as exc:
        raise ProtocolError(E_MALFORMED, "Invalid timestamp") from exc
    if head[3] not in ENCRYPTION_METHODS:
        raise ProtocolError(E_BAD_ENCRYPTION, f"Unsupported encryption {head[3]!r}")

    current = time.time() if now is None else now
    if time_window > 0 and abs(current - ts) > time_window:
        raise ProtocolError(
            E_REQUEST_EXPIRED,
            f"Timestamp off by {abs(current - ts):.0f}s (limit {time_window}s)",
        )

    # v1.1 追加的客户端信息（[4] [5] [6]）缺省为空
    extra = head[4:7]
    extra += [""] * (3 - len(extra))
    client_version, platform, locale = extra[0], extra[1], extra[2]

    # --- 2. 正文两行 ---------------------------------------------------
    body = [p.split("|") for p in (_decode(lines[1]), _decode(lines[2]))]
    if any(len(part) != 2 for part in body):
        raise ProtocolError(E_MALFORMED, "Body lines need exactly 2 fields")
    action = body[0][0].strip()
    action_data = body[0][1].strip()
    device_id_hex = body[1][0].strip()
    user_id = body[1][1].strip()
    if action == "":
        raise ProtocolError(E_MALFORMED, "Empty action")

    # --- 3. 请求尾 --------------------------------------------------------
    tail = [p.strip() for p in _decode(lines[3]).split("|")]
    if len(tail) != 2:
        raise ProtocolError(E_MALFORMED, "Tail needs 2 fields")
    plain_hash, cipher_hash = tail

    body_raw = lines[1] + b"\n" + lines[2]
    expected = hashlib.sha256(body_raw).hexdigest()
    if not hmac.compare_digest(plain_hash.lower(), expected):
        raise ProtocolError(
            E_BAD_PLAIN_HASH,
            "Plain hash mismatch (hash the two body lines you send, byte for byte)",
        )
    # encryption=none 时密文就是明文；接入真正的加密后这里换成密文哈希
    if not hmac.compare_digest(cipher_hash.lower(), expected):
        raise ProtocolError(E_BAD_CIPHER_HASH)

    return Request(
        version=version,
        ts=ts,
        encryption=head[3],
        client_version=client_version,
        platform=platform,
        locale=locale,
        action=action,
        action_data=action_data,
        device_id_hex=device_id_hex,
        user_id=user_id,
        plain_hash=plain_hash.lower(),
        cipher_hash=cipher_hash.lower(),
        body_raw=body_raw,
        body_hash=expected,
    )


# ---------------------------------------------------------------------------
# 构造
# ---------------------------------------------------------------------------

def _json(payload) -> str:
    """紧凑 JSON；ensure_ascii=False 保留中文，但不允许出现裸换行。"""
    if payload is None:
        return "{}"
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("\r", " ").replace("\n", " ")


def _sanitize(text: str) -> str:
    """竖线/换行会撕裂单行帧，统一换成空格。"""
    return str(text).replace("|", "/").replace("\r", " ").replace("\n", " ")


def encode_ok(action: str, payload=None) -> bytes:
    return f"OK | {action} | {_json(payload)}\n".encode("utf-8")


def encode_fail(action: str, code: str, message: str | None = None) -> bytes:
    msg = _sanitize(message or ERROR_MESSAGES.get(code, code))
    return f"FAIL | {action} | {code} | {msg}\n".encode("utf-8")


def encode_push(topic: str, payload=None) -> bytes:
    return f"PUSH | {topic} | {_json(payload)}\n".encode("utf-8")


def encode_protocol_error(err: ProtocolError) -> bytes:
    return encode_fail("-", err.code, err.message)


# ---------------------------------------------------------------------------
# 客户端侧构造 / 解析（测试脚本、文档示例与 GDScript 实现对齐同一份规则）
# ---------------------------------------------------------------------------

def build_request(
    action: str,
    action_data: str = "",
    device_id_hex: str = "",
    user_id: str = "",
    *,
    ts: int | None = None,
    version: str = CURRENT_VERSION,
    encryption: str = "none",
    client_version: str = "test",
    platform: str = "test",
    locale: str = "zh_CN",
) -> bytes:
    """拼一条合法报文（以单个 ``\\n`` 结尾）。"""
    ts = int(time.time()) if ts is None else ts
    head = (
        f"{MAGIC} | {version} | {ts} | {encryption} | "
        f"{client_version} | {platform} | {locale}"
    )
    body1 = f"{action} | {action_data}"
    body2 = f"{device_id_hex} | {user_id}"
    digest = hashlib.sha256((body1 + "\n" + body2).encode("utf-8")).hexdigest()
    return f"{head}\n{body1}\n{body2}\n{digest} | {digest}\n".encode("utf-8")


@dataclass
class Response:
    """服务端回帧的解析结果（客户端/测试用）。"""

    kind: str                 # OK / FAIL / PUSH / UNKNOWN
    action: str = ""
    topic: str = ""
    code: str = ""
    message: str = ""
    data: dict = field(default_factory=dict)
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "OK"


def parse_response(line: str) -> Response:
    """解析服务端单行帧（OK / FAIL / PUSH）。

    OK / PUSH 的 JSON 体可能含 ``|``（例如用户名称），所以只切前两段；
    FAIL 的 message 也可能含 ``|``，所以末段整体留给 message。
    """
    text = line.strip()
    kind, _, rest = text.partition("|")
    kind = kind.strip()

    if kind == "OK":
        action, _, body = rest.partition("|")
        return Response(kind="OK", action=action.strip(), data=_loads(body), raw=text)

    if kind == "PUSH":
        topic, _, body = rest.partition("|")
        return Response(kind="PUSH", topic=topic.strip(), data=_loads(body), raw=text)

    if kind == "FAIL":
        action, _, rest2 = rest.partition("|")
        code, _, message = rest2.partition("|")
        return Response(
            kind="FAIL",
            action=action.strip(),
            code=code.strip(),
            message=message.strip(),
            raw=text,
        )

    return Response(kind="UNKNOWN", message=text, raw=text)


def _loads(text: str) -> dict:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}

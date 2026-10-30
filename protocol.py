"""套接字协议（v1.3）：报文解析 / 响应构造 / 错误码。

完整规范见 protocol.md + flows.md。举要：

请求（4 行，行间 ``\\n``，整体以 ``\\n`` 结尾作为帧边界）::

    TheDoorOfBings | 1.3 | <ts> | <encryption> | <client_version> | <platform> | <locale>
    <action> | <action_data>[ ;; <action> | <action_data>]*
    <session_token> | <device_id_hex> | <user_id>
    <plain_hash> | <cipher_hash>

v1.3 的两处不兼容变更：

* **请求尾哈希改为「请求头 + 正文」联合哈希**：
  ``plain_hash = sha256(第1行 + "\\n" + 第2行 + "\\n" + 第3行)``（逐字节，不 strip）。
  v1.2 及更早仍按「仅正文两行」计算，服务端按 version 自动区分。
* **第 2 行支持批量多 action**（仅 v1.3）：多个 ``action|data`` 组用 ``;;`` 连接，
  服务端逐个执行、**按序返回多行** OK/FAIL。

第 3 行 v1.2 起扩展为 3 字段（session_token | device_id | user_id），
v1.1 客户端仍发 2 字段（device_id | user_id），服务端自动兼容。

响应 / 推送（单行）::

    OK   | <action> | <json>
    FAIL | <action> | <code> | <message>

**v1.3 起服务端不再主动向客户端发任何帧**（取消 10Hz 世界广播、建连 token 推送、
join/leave 事件广播）；一切数据都由客户端按需请求（``peers`` / ``world`` / ``state``），
询问频率由客户端自行决定。``PUSH`` 帧类型保留仅为兼容老服务端，本服务端不再下发。

所有协议级错误也统一走 ``FAIL | - | <code> | <message>``。
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
## 接受的历史版本。1.0 报文头只有 4 个字段，1.1 追加了客户端信息，
## 1.2 加入会话 Token，1.3 改为「头+正文」联合哈希并支持批量多 action。
SUPPORTED_VERSIONS = ("1.0", "1.1", "1.2", "1.3")
CURRENT_VERSION = "1.3"
## 当前只支持不加密；引入加密后把新算法加进来即可
ENCRYPTION_METHODS = ("none",)

## 批量请求里分隔多个 ``action|data`` 组的分隔符（仅 v1.3）。
BATCH_SEP = ";;"
## 「头+正文」联合哈希、批量多 action 的最低版本（含）。
COMBINED_HASH_MIN_VERSION = (1, 3)


def _version_tuple(version: str) -> tuple[int, ...]:
    """``"1.3"`` -> ``(1, 3)``；解析失败回 ``(0, 0)``（当作最旧处理）。"""
    try:
        return tuple(int(p) for p in version.split("."))
    except (ValueError, AttributeError):
        return (0, 0)


def uses_combined_hash(version: str) -> bool:
    """该版本是否用「请求头 + 正文」联合哈希（v1.3 起）。"""
    return _version_tuple(version) >= COMBINED_HASH_MIN_VERSION


# ---------------------------------------------------------------------------
# 批量：一条请求里的单个动作
# ---------------------------------------------------------------------------

@dataclass
class Call:
    """一条请求内的一个动作（v1.3 批量时一行正文可含多个）。"""

    action: str
    action_data: str

## 请求报文共几行
PACKET_LINES = 4
## 推送帧的 topic
TOPIC_WORLD = "world"
TOPIC_EVENT = "event"
TOPIC_TOKEN = "token"
TOPIC_RESET_TOKEN = "reset_token"

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
E_BAD_TOKEN = "BAD_SESSION_TOKEN"
E_TOKEN_EXPIRED = "SESSION_TOKEN_EXPIRED"
E_FILE_HASH_MISMATCH = "FILE_HASH_MISMATCH"

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
    E_BAD_TOKEN: "Invalid session token",
    E_TOKEN_EXPIRED: "Session token expired, reconnect",
    E_FILE_HASH_MISMATCH: "Login file hash mismatch, client files were modified",
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
    session_token: str
    device_id_hex: str
    user_id: str
    plain_hash: str
    cipher_hash: str
    ## 正文两行原文（用于防重放指纹，不含行尾换行）
    body_raw: bytes = b""
    ## 正文 sha256 hexdigest（v1.3 为头+正文联合哈希，v1.2 及以前为仅正文）
    body_hash: str = ""
    ## v1.3 批量：一条请求里的多个动作（至少一个；action/action_data 反映第一个）
    calls: list["Call"] = field(default_factory=list)

    def iter_calls(self) -> list["Call"]:
        """要执行的动作列表（批量时多个，否则单个）。"""
        return self.calls or [Call(self.action, self.action_data)]

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

    @property
    def has_token(self) -> bool:
        return self.session_token != ""


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
    combined = uses_combined_hash(version)
    line2_text = _decode(lines[1])
    if combined and BATCH_SEP in line2_text:
        # v1.3 批量：多个 ``action|data`` 组用 ``;;`` 连接
        calls: list[Call] = []
        for group in line2_text.split(BATCH_SEP):
            gparts = group.split("|", 1)
            if len(gparts) != 2:
                raise ProtocolError(
                    E_MALFORMED, "Each batched action needs '<action>|<data>'"
                )
            g_action = gparts[0].strip()
            g_data = gparts[1].strip()
            if g_action == "":
                raise ProtocolError(E_MALFORMED, "Empty action in batch")
            calls.append(Call(g_action, g_data))
        action, action_data = calls[0].action, calls[0].action_data
    else:
        # 单动作：第 2 行固定 2 字段（action | action_data）
        line2_parts = line2_text.split("|")
        if len(line2_parts) != 2:
            raise ProtocolError(E_MALFORMED, "Line 2 needs exactly 2 fields")
        action = line2_parts[0].strip()
        action_data = line2_parts[1].strip()
        if action == "":
            raise ProtocolError(E_MALFORMED, "Empty action")
        calls = [Call(action, action_data)]

    # 第 3 行允许 2 字段（v1.1）或 3 字段（v1.2）
    line3_parts = [p.strip() for p in _decode(lines[2]).split("|")]
    if len(line3_parts) == 3:
        session_token, device_id_hex, user_id = line3_parts
    elif len(line3_parts) == 2:
        session_token, device_id_hex, user_id = "", line3_parts[0], line3_parts[1]
    else:
        raise ProtocolError(E_MALFORMED, "Line 3 needs 2 or 3 fields")

    # --- 3. 请求尾 --------------------------------------------------------
    tail = [p.strip() for p in _decode(lines[3]).split("|")]
    if len(tail) != 2:
        raise ProtocolError(E_MALFORMED, "Tail needs 2 fields")
    plain_hash, cipher_hash = tail

    body_raw = lines[1] + b"\n" + lines[2]
    # v1.3：联合哈希覆盖「请求头 + 正文两行」；v1.2 及以前：仅正文两行。
    # 逐字节对客户端实际发出的原文计算，不做 strip。
    sign_raw = (lines[0] + b"\n" + body_raw) if combined else body_raw
    expected = hashlib.sha256(sign_raw).hexdigest()
    if not hmac.compare_digest(plain_hash.lower(), expected):
        raise ProtocolError(
            E_BAD_PLAIN_HASH,
            "Plain hash mismatch (v1.3 hash header+body byte for byte; older hash the two body lines)",
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
        session_token=session_token,
        device_id_hex=device_id_hex,
        user_id=user_id,
        plain_hash=plain_hash.lower(),
        cipher_hash=cipher_hash.lower(),
        body_raw=body_raw,
        body_hash=expected,
        calls=calls,
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
    action: str = "",
    action_data: str = "",
    device_id_hex: str = "",
    user_id: str = "",
    *,
    session_token: str = "",
    ts: int | None = None,
    version: str = CURRENT_VERSION,
    encryption: str = "none",
    client_version: str = "test",
    platform: str = "test",
    locale: str = "zh_CN",
    calls: list[tuple[str, str]] | None = None,
) -> bytes:
    """拼一条合法报文（以单个 ``\\n`` 结尾）。

    v1.3：默认使「头+正文」联合哈希；传 ``calls=[(a1,d1),(a2,d2),...]`` 时
    第 2 行拼接为 ``a1|d1;;a2|d2``（批量）。
    v1.2 及以前：仍按「仅正文」哈希（老客户端逐字节兼容）。

    session_token 非空时第 3 行输出 3 字段；为空时输出 2 字段（v1.1 兼容）。
    """
    ts = int(time.time()) if ts is None else ts
    head = (
        f"{MAGIC} | {version} | {ts} | {encryption} | "
        f"{client_version} | {platform} | {locale}"
    )
    if calls:
        body1 = BATCH_SEP.join(f"{a} | {d}" for a, d in calls)
    else:
        body1 = f"{action} | {action_data}"
    if session_token:
        body2 = f"{session_token} | {device_id_hex} | {user_id}"
    else:
        body2 = f"{device_id_hex} | {user_id}"
    if uses_combined_hash(version):
        sign_src = head + "\n" + body1 + "\n" + body2
    else:
        sign_src = body1 + "\n" + body2
    digest = hashlib.sha256(sign_src.encode("utf-8")).hexdigest()
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

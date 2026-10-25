"""设备 ID 的生成与无状态校验。

设计要点（沿用原 `my/device_id.py`）：

* **无状态**：合法性只靠自带的 HMAC tag + HKDF 扩展，不查库、不建会话。
* **服务端签发**：客户端不持密钥，只向服务端要一张已签名的 ID 然后原样保存。
  （客户端自签的方案等于把密钥发给所有人，而且密钥一旦泄露无法回收。）
* **不可篡改**：改任意一个字节（包括末尾的驼峰扩展段）都会校验失败。

结构（总长 1024 / 2048 字节）::

    [ver(2)][lenFlag(2)][ts(4)][nonce(32)][fp(16)][tag(32)][HKDF扩展(N)]
    |<------------ 88 字节头部 -------------->|<- 填满剩余长度 ->|

* ``tag = HMAC-SHA256(secret, ver|lenFlag|ts | nonce | fp)``
* 扩展段 = 以 tag 为密钥、以计数器为消息逐块 HMAC-SHA256 拼接后截断
* 指纹 ``fp`` 只存 sha256(硬件指纹) 的前 16 字节，原始指纹不上送

密钥来自环境变量 ``DEVICE_ID_SECRET``（由 config 在启动时 ``configure()`` 注入），
未配置时任何签发/校验都会抛 ``DeviceIdError``，
而不会默默返回 False（旧版未配密钥时整个请求线程直接捧异常，客户端收不到任何响应）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import struct
import time

# ---------------------------------------------------------------------------
# 密钥（启动时由 config.configure() 注入；未注入时回退到环境变量）
# ---------------------------------------------------------------------------
_secret: bytes | None = None

# ---------------------------------------------------------------------------
# 结构常量
# ---------------------------------------------------------------------------
_VERSION = 0x0001
_NONCE_SIZE = 32
_FP_SIZE = 16
_TAG_SIZE = 32   # HMAC-SHA256 输出长度
_HEADER_SIZE = 8 + _NONCE_SIZE + _FP_SIZE + _TAG_SIZE  # = 88 B

_LEN_FLAGS = {1024: 0x0400, 2048: 0x0800}
_FLAG_LENS = {v: k for k, v in _LEN_FLAGS.items()}
DEFAULT_LENGTH = 1024

## 最短密钥长度（低于这个长度直接拒启）
MIN_SECRET_BYTES = 16


class DeviceIdError(RuntimeError):
    """设备 ID 子系统不可用（没配密钥等）。"""


# ---------------------------------------------------------------------------
# 密钥
# ---------------------------------------------------------------------------

def configure(secret: str | None = None) -> None:
    """注入密钥（应在服务启动时调一次）。"""
    global _secret
    raw = (secret if secret is not None else os.environ.get("DEVICE_ID_SECRET", "")).strip()
    _secret = raw.encode("utf-8") if raw else None


def has_secret() -> bool:
    return bool(_key())


def _key() -> bytes:
    if _secret is None:
        # 没 configure 过就直接读环境变量，便于测试脚本 import 即用
        configure()
    if not _secret:
        return b""
    return _secret


def _require_key() -> bytes:
    key = _key()
    if not key:
        raise DeviceIdError(
            "DEVICE_ID_SECRET 未配置：请在 .env 里写一个至少 32 字节的随机密钥。"
        )
    return key


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _compute_tag(prefix: bytes, nonce: bytes, fp: bytes) -> bytes:
    """HMAC-SHA256 over header fields（不含 expansion）。"""
    return hmac.new(_require_key(), prefix + nonce + fp, hashlib.sha256).digest()


def _expand(tag: bytes, length: int) -> bytes:
    """HKDF-style 确定性扩展：用 tag 作密钥逐块产生，拼接后截断。"""
    out = b""
    counter = 0
    while len(out) < length:
        out += hmac.new(tag, struct.pack(">I", counter), hashlib.sha256).digest()
        counter += 1
    return out[:length]


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def DeviceId_get(length: int = DEFAULT_LENGTH, device_fingerprint: bytes = b"") -> bytes:
    """签发一张设备 ID。

    Parameters
    ----------
    length
        1024 或 2048，ID 总字节数
    device_fingerprint
        客户端硬件指纹原始字节；留空则用随机指纹（不绑定硬件，仅用于测试）
    """
    if length not in _LEN_FLAGS:
        raise ValueError(f"length 必须是 1024 或 2048，得到 {length}")

    len_flag = _LEN_FLAGS[length]
    ts = int(time.time())
    nonce = secrets.token_bytes(_NONCE_SIZE)

    if device_fingerprint:
        fp = hashlib.sha256(device_fingerprint).digest()[:_FP_SIZE]
    else:
        fp = secrets.token_bytes(_FP_SIZE)

    prefix = struct.pack(">HHI", _VERSION, len_flag, ts)
    tag = _compute_tag(prefix, nonce, fp)
    expansion = _expand(tag, length - _HEADER_SIZE)
    return prefix + nonce + fp + tag + expansion


def DeviceId_can_use(device_id: bytes, max_age_seconds: int = 0) -> bool:
    """无状态校验设备 ID。

    验证链（任意一步失败即 False）
    ------------------------------
    1. 总长度是否 1024 / 2048
    2. version 字段是否已知
    3. length_flag 是否与实际长度一致
    4. HMAC tag 是否与头部字段匹配（防伪造核心）
    5. expansion 是否由 tag 正确派生（防任意字节篡改）
    6. （可选）时间戳是否在 max_age_seconds 内（0 = 不过期）
    """
    if not device_id or len(device_id) not in _LEN_FLAGS:
        return False

    prefix = device_id[:8]
    nonce = device_id[8 : 8 + _NONCE_SIZE]
    fp = device_id[8 + _NONCE_SIZE : 8 + _NONCE_SIZE + _FP_SIZE]
    stored_tag = device_id[8 + _NONCE_SIZE + _FP_SIZE : _HEADER_SIZE]
    expansion = device_id[_HEADER_SIZE:]

    ver, len_flag, ts = struct.unpack(">HHI", prefix)
    if ver != _VERSION:
        return False
    if _LEN_FLAGS.get(len(device_id)) != len_flag:
        return False

    if not hmac.compare_digest(stored_tag, _compute_tag(prefix, nonce, fp)):
        return False
    if not hmac.compare_digest(expansion, _expand(stored_tag, len(expansion))):
        return False

    if max_age_seconds > 0 and abs(int(time.time()) - ts) > max_age_seconds:
        return False
    return True


def DeviceId_age(device_id: bytes) -> int | None:
    """设备 ID 的签发时间到现在的秒数（头部不可解析时返回 None）。"""
    if len(device_id) < 8:
        return None
    try:
        _, _, ts = struct.unpack(">HHI", device_id[:8])
    except struct.error:
        return None
    return int(time.time()) - ts


def DeviceId_fingerprint(device_id: bytes) -> bytes:
    """取出 ID 里的指纹段（16 字节，已是 sha256 截断后的值）。"""
    return device_id[8 + _NONCE_SIZE : 8 + _NONCE_SIZE + _FP_SIZE]


def normalize_length(value: int | str | None) -> int:
    """把外部传入的长度归一成 1024 / 2048，非法值回默认值。"""
    try:
        length = int(value) if value not in (None, "") else DEFAULT_LENGTH
    except (TypeError, ValueError):
        return DEFAULT_LENGTH
    return length if length in _LEN_FLAGS else DEFAULT_LENGTH


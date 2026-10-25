"""设备ID的生成与无状态校验。"""

import hashlib
import hmac
import os
import secrets
import struct
import time

# ---------------------------------------------------------------------------
# 密钥:生产环境必须从环境变量读取,不得使用默认值
# ---------------------------------------------------------------------------
_SECRET_KEY: bytes = os.environ.get(
    "DEVICE_ID_SECRET",
    ""
).encode()

# 结构常量
_VERSION      = 0x0001
_NONCE_SIZE   = 32
_FP_SIZE      = 16
_TAG_SIZE     = 32   # HMAC-SHA256 输出长度
_HEADER_SIZE  = 8 + _NONCE_SIZE + _FP_SIZE + _TAG_SIZE  # = 88 B

_LEN_FLAGS    = {1024: 0x0400, 2048: 0x0800}
_FLAG_LENS    = {v: k for k, v in _LEN_FLAGS.items()}

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _require_key() -> bytes:
    if not _SECRET_KEY:
        raise RuntimeError(
            "DEVICE_ID_SECRET 环境变量未设置,无法生成/验证设备ID。"
            "请设置一个至少 32 字节的随机密钥。"
        )
    return _SECRET_KEY


def _compute_tag(prefix: bytes, nonce: bytes, fp: bytes) -> bytes:
    """HMAC-SHA256 over header fields(不含 expansion)。"""
    return hmac.new(_require_key(), prefix + nonce + fp, hashlib.sha256).digest()


def _expand(tag: bytes, length: int) -> bytes:
    """
    HKDF-style 确定性扩展:用 tag 作为密钥,计数器作为信息,
    逐块产生 32 字节,拼接后截取到目标长度。
    """
    out = b""
    counter = 0
    while len(out) < length:
        block = hmac.new(
            tag,
            struct.pack(">I", counter),
            hashlib.sha256,
        ).digest()
        out += block
        counter += 1
    return out[:length]


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def DeviceId_get(length: int = 1024, device_fingerprint: bytes = b"") -> bytes:
    """
    生成设备ID。

    Parameters
    ----------
    length : 1024 或 2048,ID 总字节数
    device_fingerprint : 设备硬件指纹(如 MAC、序列号的拼接)；
                         留空则使用随机字节(无法绑定设备,仅用于测试)

    Returns
    -------
    bytes  长度为 *length* 的字节串

    结构
    ----
    [ver(2)][lenFlag(2)][ts(4)][nonce(32)][fp(16)][tag(32)][expansion(N)]
    """
    if length not in _LEN_FLAGS:
        raise ValueError(f"length 必须是 1024 或 2048,得到 {length}")

    len_flag = _LEN_FLAGS[length]
    ts       = int(time.time())
    nonce    = secrets.token_bytes(_NONCE_SIZE)         # 32 B 随机数

    if device_fingerprint:
        fp = hashlib.sha256(device_fingerprint).digest()[:_FP_SIZE]
    else:
        fp = secrets.token_bytes(_FP_SIZE)              # 无指纹时用随机 16 B

    prefix = struct.pack(">HHI", _VERSION, len_flag, ts)  # 8 B
    tag    = _compute_tag(prefix, nonce, fp)               # 32 B

    expansion = _expand(tag, length - _HEADER_SIZE)        # 填满剩余字节

    return prefix + nonce + fp + tag + expansion


def DeviceId_can_use(device_id: bytes, max_age_seconds: int = 0) -> bool:
    """
    无状态验证设备ID合法性(不需要数据库)。

    验证链(任意一步失败即返回 False)
    ----------
    1. 总长度是否为 1024 或 2048
    2. version 字段是否为已知版本
    3. length_flag 是否与实际长度一致
    4. HMAC tag 是否与头部字段匹配(防伪造核心)
    5. expansion 是否由 tag 正确派生(防任意字节篡改)
    6. (可选)时间戳是否在 max_age_seconds 内(0 = 不限制)

    Parameters
    ----------
    device_id        : 待验证的字节串
    max_age_seconds  : 有效期(秒),0 表示不过期
    """
    # 1. 长度
    if len(device_id) not in _LEN_FLAGS:
        return False

    # 2-3. 解析并校验头部字段
    prefix  = device_id[:8]
    nonce   = device_id[8 : 8 + _NONCE_SIZE]
    fp      = device_id[8 + _NONCE_SIZE : 8 + _NONCE_SIZE + _FP_SIZE]
    stored_tag = device_id[8 + _NONCE_SIZE + _FP_SIZE : _HEADER_SIZE]
    expansion  = device_id[_HEADER_SIZE :]

    ver, len_flag, ts = struct.unpack(">HHI", prefix)
    if ver != _VERSION:
        return False
    if _LEN_FLAGS.get(len(device_id)) != len_flag:
        return False

    # 4. 验证 HMAC tag
    expected_tag = _compute_tag(prefix, nonce, fp)
    if not hmac.compare_digest(stored_tag, expected_tag):
        return False

    # 5. 验证 expansion(篡改任意一字节都会导致此步失败)
    expected_expansion = _expand(stored_tag, len(expansion))
    if not hmac.compare_digest(expansion, expected_expansion):
        return False

    # 6. 可选时间窗口
    if max_age_seconds > 0:
        if abs(int(time.time()) - ts) > max_age_seconds:
            return False

    return True

"""账号业务：设备登记 / 注册 / 登录。

约定（沿原 `my/users.py`，按新决策调整）：

* **一个设备一个账号**，账号一旦绑定设备不可迁移、不可换机登录；
* ``user_id`` 由 **服务端生成**（客户端只提交 ``user_name``），
  形如 ``ZWME-2WKQ``，字符集去掉了容易看错的 I / O / 0 / 1；
* 设备 ID 由服务端签发（见 `device_id.py`），这里只负责「登记 + 绑定」。

所有方法失败时返回 ``(False, 错误码, 上下文)``，不抛异常，
方便调用方直接映射成协议帧。错误码定义在 `protocol.py` 的 ``E_*``。
"""

from __future__ import annotations

import logging
import re
import secrets
import time

import protocol
from store import StoreConflict, StoreError, Store

log = logging.getLogger(__name__)

## 用户 ID 字符集：去掉了 I / O / 0 / 1（口头传播容易听错）
USER_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
USER_ID_PATTERN = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
## 用户名称不允许出现的字符：竖线会掰弯单行帧，& 会掰弯参数分隔
_INVALID_NAME_CHARS = set("|&\r\n\t")


def generate_user_id() -> str:
    """生成一个形如 ``ZWME-2WKQ`` 的候选 ID。"""
    pick = lambda: "".join(secrets.choice(USER_ID_ALPHABET) for _ in range(4))  # noqa: E731
    return f"{pick()}-{pick()}"


def validate_user_name(name: str, max_len: int = 24) -> tuple[bool, str]:
    """校验用户名称；返回 ``(ok, 原因)``。"""
    if name == "":
        return False, "用户名称不能为空"
    if len(name) > max_len:
        return False, f"用户名称最多 {max_len} 个字符"
    bad = sorted(set(name) & _INVALID_NAME_CHARS)
    if bad:
        shown = " ".join(repr(c) for c in bad)
        return False, f"用户名称不能包含 {shown}"
    if any(ord(c) < 32 for c in name):
        return False, "用户名称不能包含控制字符"
    return True, ""


def is_valid_user_id(user_id: str) -> bool:
    return bool(USER_ID_PATTERN.match(user_id))


class user:
    """账号业务操作类（名字沿用原实现的 ``user``）。"""

    ## 生成 ID 撞车时的最大重试次数（4-4 字符空间足够大，基本上一次就成）
    MAX_ID_ATTEMPTS = 8

    def __init__(self, store: Store, user_name_max_len: int = 24) -> None:
        self.store = store
        self.user_name_max_len = user_name_max_len

    # ------------------------------------------------------------------ #
    # 设备
    # ------------------------------------------------------------------ #

    def touch_device(
        self,
        device_id_hex: str,
        client_version: str = "",
        platform: str = "",
        ip: str = "",
    ) -> dict | None:
        """登记/刷新设备行，并返回该设备已绑定的账号（没绑则 None）。"""
        self.store.register_device(device_id_hex, client_version, platform, ip)
        return self.store.account_by_device(device_id_hex)

    def account_by_device(self, device_id_hex: str) -> dict | None:
        return self.store.account_by_device(device_id_hex)

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #

    def register(
        self,
        user_name: str,
        device_id_hex: str,
        client_version: str = "",
        platform: str = "",
        ip: str = "",
    ) -> tuple[bool, str, dict]:
        """注册账号（服务端分配 user_id）。

        返回 ``(ok, 错误码, 上下文)``；成功时上下文为
        ``{"user_id", "user_name"}``；失败时可能带 ``account``（设备已绑定时）。
        """
        name = user_name.strip()
        ok, reason = validate_user_name(name, self.user_name_max_len)
        if not ok:
            return False, protocol.E_BAD_PAYLOAD, {"message": reason}

        if not device_id_hex:
            return False, protocol.E_BAD_DEVICE_ID, {"message": "设备 ID 不能为空"}

        # 设备已绑定账号：不再建新号，直接告诉客户端“去用登录”（并带回账号，方便找回）
        bound = self.store.account_by_device(device_id_hex)
        if bound is not None:
            return False, protocol.E_DEVICE_ALREADY_BOUND, {
                "account": {
                    "user_id": bound["user_id"],
                    "user_name": bound["user_name"],
                }
            }

        self.store.register_device(device_id_hex, client_version, platform, ip)

        for _ in range(self.MAX_ID_ATTEMPTS):
            new_id = generate_user_id()
            try:
                self.store.create_account(new_id, name, device_id_hex)
            except StoreConflict as exc:
                if exc.field == "user_name":
                    return False, protocol.E_NAME_TAKEN, {}
                if exc.field == "device_id":
                    return False, protocol.E_DEVICE_ALREADY_BOUND, {}
                # user_id 撞车：换一个再试
                log.info("user_id %s 已占用，重新生成", new_id)
                continue
            except StoreError:
                log.exception("注册时存储层报错")
                return False, protocol.E_INTERNAL, {}
            return True, "", {"user_id": new_id, "user_name": name}

        return False, protocol.E_INTERNAL, {"message": "生成用户 ID 失败，请重试"}

    # ------------------------------------------------------------------ #
    # 登录
    # ------------------------------------------------------------------ #

    def login(
        self,
        user_id: str,
        device_id_hex: str,
    ) -> tuple[bool, str, dict]:
        """登录校验：账号存在 + 设备匹配 + 状态可用 + 未锁定。"""
        user_id = user_id.strip().upper()
        if not is_valid_user_id(user_id):
            return False, protocol.E_BAD_USER_ID, {"message": "用户 ID 格式不对"}
        if not device_id_hex:
            return False, protocol.E_BAD_DEVICE_ID, {"message": "设备 ID 不能为空"}

        # 先确保设备行存在：players / accounts 都对它建了外键
        try:
            self.store.register_device(device_id_hex)
        except StoreError:
            log.warning("登录前登记设备失败（不影响登录判定）", exc_info=True)

        try:
            acc = self.store.account_for_login(user_id, device_id_hex)
        except StoreError:
            log.exception("登录时存储层报错")
            return False, protocol.E_INTERNAL, {}

        if acc is None:
            return False, protocol.E_USER_NOT_FOUND, {}
        if acc.get("device_id") != device_id_hex:
            # 设备锁：不告诉客户端“这个号存在”，统一用 不匹配
            return False, protocol.E_DEVICE_MISMATCH, {}
        if acc.get("status", "active") != "active":
            return False, protocol.E_ACCOUNT_DISABLED, {}
        locked_until = acc.get("locked_until")
        if locked_until is not None and _to_ts(locked_until) > time.time():
            return False, protocol.E_ACCOUNT_LOCKED, {}

        try:
            self.store.touch_activity(user_id, device_id_hex)
        except StoreError:
            log.warning("刷新登录时间失败（不影响登录）", exc_info=True)

        return True, "", {
            "user_id": acc["user_id"],
            "user_name": acc["user_name"],
        }


def _to_ts(value) -> float:
    """把 datetime / float / None 统一成 timestamp。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value.timestamp())
    except Exception:  # noqa: BLE001 - 不可解析就当没过期
        return 0.0


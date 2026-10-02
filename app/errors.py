"""统一错误码与异常。"""

from __future__ import annotations

# 错误码 -> HTTP 状态码
STATUS: dict[str, int] = {
    "BAD_REQUEST": 400,
    "DEVICE_UNKNOWN": 404,
    "USER_NOT_FOUND": 404,
    "DEVICE_ALREADY_BOUND": 409,
    "NAME_TAKEN": 409,
    "DEVICE_MISMATCH": 403,
    "NAME_MISMATCH": 403,
    "ACCOUNT_LOCKED": 403,
    "SERVICE_UNAVAILABLE": 503,
    "INTERNAL": 500,
}


class ApiError(Exception):
    """业务错误：会被应用工厂里的 errorhandler 转成
    {"ok": false, "code": ..., "msg": ...}。
    """

    def __init__(self, code: str, msg: str, status: int | None = None):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.status = status or STATUS.get(code, 400)

    def to_dict(self) -> dict:
        return {"ok": False, "code": self.code, "msg": self.msg}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"ApiError({self.code!r}, {self.msg!r}, {self.status})"

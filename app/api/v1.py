"""众生之门 API v1。

设计原则（对应产品规则）：
  * 设备标识由服务端签发并落库；
  * 一个设备只能绑定一个账号，一个账号永久绑定首次登记的设备；
  * 每次登录（verify）都在服务端重新校验三者一致性。
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from .. import db
from ..config import Config
from ..errors import ApiError

bp = Blueprint("v1", __name__, url_prefix="/api/v1")

# 去掉容易看错的 I L O U
USER_ID_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ0123456789"
USER_ID_LEN = 8
# 允许除控制字符以外的任何可打印字符（含中文）
USER_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,%d}$" % Config.USER_NAME_MAX_LEN)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("BAD_REQUEST", "请求体必须是 JSON 对象")
    return data


def _require_text(data: dict, key: str, label: str, max_len: int) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ApiError("BAD_REQUEST", f"缺少字段 {key}（{label}）")
    value = value.strip()
    if not value:
        raise ApiError("BAD_REQUEST", f"字段 {key}（{label}）不能为空")
    if len(value) > max_len:
        raise ApiError("BAD_REQUEST", f"字段 {key}（{label}）过长")
    return value


def _new_user_id() -> str:
    raw = "".join(secrets.choice(USER_ID_ALPHABET) for _ in range(USER_ID_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def _account_for_device(cur, device_id: str):
    """查该设备已绑定的账号；没有则返回 None。"""
    cur.execute(
        "SELECT user_id, user_name, status FROM accounts WHERE device_id = %s",
        (device_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "user_id": row["user_id"],
        "user_name": row["user_name"],
        "status": row["status"],
    }


@bp.get("/health")
def health():
    """健康检查：进程存活 + 数据库连通性。"""
    return jsonify(
        ok=True,
        service=Config.SERVICE_NAME,
        version=Config.SERVICE_VERSION,
        database="up" if db.ping() else "down",
        time=_now().isoformat(timespec="seconds"),
    )


@bp.post("/device")
def issue_device():
    """签发设备标识。

    body 可为空 {} —— 总是签发一个新的 device_id；
    body 带 device_id 时幂等处理：已知则原样返回，未知则收编入库
    （用于兼容旧版本客户端已经存在本地的设备标识）。

    响应额外带 account 字段：该设备已绑定的账号（无则 null）。
    客户端可用它在本地账号文件丢失时恢复登录，而不必重新注册。
    """
    data = request.get_json(silent=True)
    if data is None or data == {}:
        data = {}
    if not isinstance(data, dict):
        raise ApiError("BAD_REQUEST", "请求体必须是 JSON 对象")

    device_id = data.get("device_id")

    if device_id is None or device_id == "":
        device_id = str(uuid.uuid4())
        with db.get_cursor() as cur:
            cur.execute(
                "INSERT INTO devices (device_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (device_id,),
            )
        return jsonify(ok=True, device_id=device_id, issued=True, account=None)

    if not isinstance(device_id, str):
        raise ApiError("BAD_REQUEST", "字段 device_id（设备标识）类型不合法")
    device_id = device_id.strip()
    if not 8 <= len(device_id) <= 64:
        raise ApiError("BAD_REQUEST", "字段 device_id（设备标识）长度不合法")

    with db.get_cursor() as cur:
        cur.execute("SELECT device_id FROM devices WHERE device_id = %s", (device_id,))
        if cur.fetchone() is None:
            cur.execute("INSERT INTO devices (device_id) VALUES (%s)", (device_id,))
        else:
            cur.execute(
                "UPDATE devices SET last_seen_at = now() WHERE device_id = %s",
                (device_id,),
            )
        account = _account_for_device(cur, device_id)
    return jsonify(ok=True, device_id=device_id, issued=False, account=account)


@bp.post("/register")
def register():
    """注册：把当前设备与一个名称绑定，服务端生成 user_id。"""
    data = _json_body()
    device_id = _require_text(data, "device_id", "设备标识", 64)
    user_name = _require_text(data, "user_name", "用户名称", Config.USER_NAME_MAX_LEN)
    if not USER_NAME_RE.match(user_name):
        raise ApiError("BAD_REQUEST", "用户名称含有非法字符")

    with db.get_cursor() as cur:
        cur.execute("SELECT status FROM devices WHERE device_id = %s", (device_id,))
        dev = cur.fetchone()
        if dev is None:
            raise ApiError("DEVICE_UNKNOWN", "设备未登记，请先调用 /api/v1/device 签发设备标识")
        if dev["status"] != "active":
            raise ApiError("ACCOUNT_LOCKED", "该设备已被停用")

        cur.execute("SELECT user_id FROM accounts WHERE device_id = %s", (device_id,))
        if cur.fetchone() is not None:
            raise ApiError("DEVICE_ALREADY_BOUND", "该设备已绑定账号，不能重复注册")

        cur.execute("SELECT user_id FROM accounts WHERE user_name = %s", (user_name,))
        if cur.fetchone() is not None:
            raise ApiError("NAME_TAKEN", "该名称已被占用，请换一个")

        user_id = None
        for _ in range(8):
            candidate = _new_user_id()
            cur.execute(
                "INSERT INTO accounts (user_id, device_id, user_name, last_login_at)"
                " VALUES (%s, %s, %s, now()) ON CONFLICT DO NOTHING",
                (candidate, device_id, user_name),
            )
            if cur.rowcount:
                user_id = candidate
                break

        if user_id is None:
            # 并发下要么设备被抢绑、要么名称被抢注
            cur.execute("SELECT user_id FROM accounts WHERE device_id = %s", (device_id,))
            if cur.fetchone() is not None:
                raise ApiError("DEVICE_ALREADY_BOUND", "该设备已绑定账号，不能重复注册")
            cur.execute("SELECT user_id FROM accounts WHERE user_name = %s", (user_name,))
            if cur.fetchone() is not None:
                raise ApiError("NAME_TAKEN", "该名称已被占用，请换一个")
            raise ApiError("INTERNAL", "注册失败，请稍后重试")

        cur.execute("UPDATE devices SET last_seen_at = now() WHERE device_id = %s", (device_id,))

    return jsonify(ok=True, user_id=user_id, user_name=user_name, device_id=device_id)


@bp.post("/verify")
def verify():
    """校验：设备 + 用户 ID + 名称三者必须完全匹配（设备锁的强制执行点）。"""
    data = _json_body()
    device_id = _require_text(data, "device_id", "设备标识", 64)
    user_id = _require_text(data, "user_id", "用户 ID", 32).upper()
    user_name = _require_text(data, "user_name", "用户名称", Config.USER_NAME_MAX_LEN)

    with db.get_cursor() as cur:
        cur.execute(
            "SELECT user_id, device_id, user_name, status, locked_until"
            " FROM accounts WHERE user_id = %s",
            (user_id,),
        )
        acc = cur.fetchone()
        if acc is None:
            raise ApiError("USER_NOT_FOUND", "账号不存在，请重新注册")
        if acc["status"] != "active":
            raise ApiError("ACCOUNT_LOCKED", "账号已被停用")
        if acc["locked_until"] is not None and acc["locked_until"] > _now():
            raise ApiError("ACCOUNT_LOCKED", "账号暂时被锁定，请稍后再试")
        if acc["device_id"] != device_id:
            raise ApiError("DEVICE_MISMATCH", "账号与当前设备不匹配（账号仅能在首次绑定的设备上使用）")
        if acc["user_name"] != user_name:
            raise ApiError("NAME_MISMATCH", "用户名称与账号不匹配")

        cur.execute("UPDATE accounts SET last_login_at = now() WHERE user_id = %s", (user_id,))
        cur.execute("UPDATE devices SET last_seen_at = now() WHERE device_id = %s", (device_id,))

    return jsonify(ok=True, user_id=user_id, user_name=user_name, device_id=device_id)

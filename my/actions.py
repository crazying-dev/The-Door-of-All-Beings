"""
套接字协议动作实现。

每个动作 handler 签名: handler(payload: str, device_id_hex: str, user_id: str) -> str
返回字符串直接回写客户端。payload 约定为 "参数1&参数2&..." 形式。

由 main.py import 本模块触发注册。
"""

import json
import time

import users
from device_id import DeviceId_can_use

# 由 GameAPI 注入的账号业务实例
_user_handler: users.user | None = None

# 动作注册表（与 main.ACTIONS 同一对象，import 后由 register 填充）
ACTIONS: dict[str, callable] = {}


def init_handlers(user_handler: users.user) -> None:
    """注入账号业务实例。"""
    global _user_handler
    _user_handler = user_handler


def action(name: str):
    """动作注册装饰器。"""
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


def _ok(action: str, result: dict) -> str:
    return f"OK | {action} | {json.dumps(result, ensure_ascii=False)}"


def _fail(action: str, error: str) -> str:
    return f"FAIL | {action} | {error}"


# ---------------------------------------------------------------------------
# 动作实现
# ---------------------------------------------------------------------------

@action("ping")
def handle_ping(payload: str, device_id_hex: str, user_id: str) -> str:
    """连通性测试。"""
    return _ok("ping", {"pong_at": int(time.time())})


@action("register")
def handle_register(payload: str, device_id_hex: str, user_id: str) -> str:
    """
    注册账号。payload: <user_name>&<user_id>
    请求正文的 [<device_id>] 段必须携带合法设备ID。
    """
    parts = payload.split("&")
    if len(parts) != 2 or not all(p.strip() for p in parts):
        return _fail("register", "Malformed payload, expect <user_name>&<user_id>")
    user_name, new_user_id = (p.strip() for p in parts)

    if _user_handler is None:
        return _fail("register", "Service not ready")
    if not DeviceId_can_use(bytes.fromhex(device_id_hex) if device_id_hex else b""):
        return _fail("register", "Invalid Device ID")

    ok, err = _user_handler.register(user_name, new_user_id, device_id_hex)
    if not ok:
        return _fail("register", err)
    return _ok("register", {"user_id": new_user_id, "user_name": user_name})


@action("login")
def handle_login(payload: str, device_id_hex: str, user_id: str) -> str:
    """
    登录。payload: <user_id>
    请求正文的 [<device_id>] 段必须携带设备ID，账号须与该设备绑定。
    """
    login_user_id = payload.strip() or user_id.strip()
    if not login_user_id:
        return _fail("login", "Malformed payload, expect <user_id>")

    if _user_handler is None:
        return _fail("login", "Service not ready")

    ok, err, info = _user_handler.login(login_user_id, device_id_hex)
    if not ok:
        return _fail("login", err)
    return _ok("login", {
        "user_id": info["user_id"],
        "user_name": info["user_name"],
    })

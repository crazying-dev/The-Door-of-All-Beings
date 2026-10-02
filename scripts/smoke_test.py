"""端到端冒烟测试：对本机服务依次调用 4 个接口，验证设备锁语义。

用法：
    uv run python scripts/smoke_test.py [base_url]
默认 base_url = http://127.0.0.1:2690

测试会在真实数据库里创建临时记录，结束后自动删除。
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:2690"

RESULTS: list[tuple[str, bool]] = []
CREATED_DEVICES: list[str] = []
CREATED_USERS: list[str] = []


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE.rstrip("/") + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + (("  |  " + detail) if detail else ""))


def main() -> int:
    print(f"== 冒烟测试 -> {BASE}\n")

    status, body = call("GET", "/api/v1/health")
    check("health 200/ok", status == 200 and body.get("ok") is True, json.dumps(body, ensure_ascii=False))
    check("health 数据库连通", body.get("database") == "up", f"database={body.get('database')}")

    # 1) 未登记设备注册 -> DEVICE_UNKNOWN
    status, body = call("POST", "/api/v1/register", {"device_id": "smoke-unknown-device-0001", "user_name": "smoke_unknown"})
    check("未知设备注册被拒", status == 404 and body.get("code") == "DEVICE_UNKNOWN", json.dumps(body, ensure_ascii=False))

    # 2) 签发设备
    status, body = call("POST", "/api/v1/device", {})
    dev_a = body.get("device_id", "")
    CREATED_DEVICES.append(dev_a)
    check("签发设备 A", status == 200 and body.get("ok") is True and bool(dev_a) and body.get("issued") is True, dev_a)

    # 3) 幂等签发
    status, body = call("POST", "/api/v1/device", {"device_id": dev_a})
    check("设备签发幂等", status == 200 and body.get("device_id") == dev_a and body.get("issued") is False and body.get("account") is None)

    # 4) 注册
    name_a = "smoke_alpha"
    status, body = call("POST", "/api/v1/register", {"device_id": dev_a, "user_name": name_a})
    user_a = body.get("user_id", "")
    CREATED_USERS.append(user_a)
    check("注册设备 A", status == 200 and body.get("ok") is True and bool(user_a), json.dumps(body, ensure_ascii=False))

    # 4b) 设备已绑定后，再查该设备应回带账号（客户端本地文件丢失时的恢复路径）
    status, body = call("POST", "/api/v1/device", {"device_id": dev_a})
    account = body.get("account") or {}
    check(
        "已绑定设备回带账号",
        status == 200 and account.get("user_id") == user_a and account.get("user_name") == name_a,
        json.dumps(body, ensure_ascii=False),
    )

    # 5) 同设备重复注册 -> DEVICE_ALREADY_BOUND（同一设备不能注册多个账号）
    status, body = call("POST", "/api/v1/register", {"device_id": dev_a, "user_name": "smoke_beta"})
    check("同设备二次注册被拒", status == 409 and body.get("code") == "DEVICE_ALREADY_BOUND", json.dumps(body, ensure_ascii=False))

    # 6) 换设备用同名 -> NAME_TAKEN
    status, body = call("POST", "/api/v1/device", {})
    dev_b = body.get("device_id", "")
    CREATED_DEVICES.append(dev_b)
    status, body = call("POST", "/api/v1/register", {"device_id": dev_b, "user_name": name_a})
    check("同名跨设备注册被拒", status == 409 and body.get("code") == "NAME_TAKEN", json.dumps(body, ensure_ascii=False))

    # 7) 正常校验
    status, body = call("POST", "/api/v1/verify", {"device_id": dev_a, "user_id": user_a, "user_name": name_a})
    check("verify 通过", status == 200 and body.get("ok") is True, json.dumps(body, ensure_ascii=False))

    # 8) 跨设备校验 -> DEVICE_MISMATCH（设备锁）
    status, body = call("POST", "/api/v1/verify", {"device_id": dev_b, "user_id": user_a, "user_name": name_a})
    check("跨设备登录被拒", status == 403 and body.get("code") == "DEVICE_MISMATCH", json.dumps(body, ensure_ascii=False))

    # 9) 改名 -> NAME_MISMATCH
    status, body = call("POST", "/api/v1/verify", {"device_id": dev_a, "user_id": user_a, "user_name": "smoke_zzz"})
    check("改名校验被拒", status == 403 and body.get("code") == "NAME_MISMATCH", json.dumps(body, ensure_ascii=False))

    # 10) 不存在的 user_id -> USER_NOT_FOUND
    status, body = call("POST", "/api/v1/verify", {"device_id": dev_a, "user_id": "ZZZZ-ZZZZ", "user_name": name_a})
    check("未知用户校验被拒", status == 404 and body.get("code") == "USER_NOT_FOUND", json.dumps(body, ensure_ascii=False))

    # 11) 缺字段 -> BAD_REQUEST
    status, body = call("POST", "/api/v1/verify", {"device_id": dev_a})
    check("缺字段返回 BAD_REQUEST", status == 400 and body.get("code") == "BAD_REQUEST", json.dumps(body, ensure_ascii=False))

    # 12) 非 JSON -> BAD_REQUEST
    req = urllib.request.Request(BASE.rstrip("/") + "/api/v1/register", data=b"not-json", method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status, body = resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status, body = exc.code, json.loads(exc.read().decode("utf-8"))
    check("非 JSON 体返回 BAD_REQUEST", status == 400 and body.get("code") == "BAD_REQUEST", json.dumps(body, ensure_ascii=False))

    cleanup()

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    if failed:
        print("失败项：" + ", ".join(failed))
        return 1
    return 0


def cleanup() -> None:
    """删除本次测试创建的临时记录。"""
    if not (CREATED_DEVICES or CREATED_USERS):
        return
    try:
        from app import db

        with db.get_cursor(dict_rows=False) as cur:
            if CREATED_USERS:
                cur.execute("DELETE FROM accounts WHERE user_id = ANY(%s)", (CREATED_USERS,))
            if CREATED_DEVICES:
                cur.execute("DELETE FROM devices WHERE device_id = ANY(%s)", (CREATED_DEVICES,))
        print("[cleanup] 临时记录已删除")
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] 失败（请手动清理）：{exc!r}")


if __name__ == "__main__":
    raise SystemExit(main())

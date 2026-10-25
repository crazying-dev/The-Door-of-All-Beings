"""用户账号业务：注册 / 登录（对应 schema.sql 的 devices / accounts 表）。"""

import time

from psycopg2 import sql
from psycopg2.errors import UniqueViolation

from device_id import DeviceId_can_use


class user:
    """
    账号业务操作类。

    device_id 在协议与库中均以 hex 字符串传输/存储（生成端是 bytes）。
    """

    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #

    def register(self, user_name: str, user_id: str, device_id_hex: str) -> tuple[bool, str]:
        """
        注册账号：设备首次入库并永久绑定该账号（一个设备一个账号）。

        成功: (True,  "")
        失败: (False, 原因)
        """
        # 设备ID密码学校验（无状态）
        device_id = bytes.fromhex(device_id_hex) if self._is_hex(device_id_hex) else b""
        if not DeviceId_can_use(device_id):
            return False, "Invalid Device ID"

        with self.db.conn() as conn, conn.cursor() as cur:
            # 1. 设备登记（ON CONFLICT 保持已有绑定不变）
            cur.execute(
                sql.SQL(
                    "INSERT INTO devices (device_id) VALUES (%s) "
                    "ON CONFLICT (device_id) DO NOTHING"
                ),
                (device_id_hex,),
            )
            # 2. 账号入库
            try:
                cur.execute(
                    sql.SQL(
                        "INSERT INTO accounts (user_id, device_id, user_name) "
                        "VALUES (%s, %s, %s)"
                    ),
                    (user_id, device_id_hex, user_name),
                )
            except UniqueViolation:
                # 事务已由 Database.conn() 自动 rollback
                return False, "Username or user id already exists"
        return True, ""

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #

    def exists(self, user_id: str, device_id_hex: str) -> bool:
        """账号存在且与设备绑定一致（用于协议层 user_id 校验）。"""
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT 1 FROM accounts WHERE user_id = %s AND device_id = %s"
                ),
                (user_id, device_id_hex),
            )
            return cur.fetchone() is not None

    # ------------------------------------------------------------------ #
    # 登录
    # ------------------------------------------------------------------ #

    def login(self, user_id: str, device_id_hex: str) -> tuple[bool, str, dict]:
        """
        登录校验：账号存在、设备匹配（三元组之 设备+用户ID）、状态可用、未锁定。

        成功: (True, "", 账号信息 dict)
        失败: (False, 原因, {})
        """
        with self.db.conn() as conn, conn.cursor(
            cursor_factory=self._dict_cursor()
        ) as cur:
            cur.execute(
                sql.SQL(
                    "SELECT user_id, user_name, status, locked_until "
                    "FROM accounts WHERE user_id = %s AND device_id = %s"
                ),
                (user_id, device_id_hex),
            )
            row = cur.fetchone()
            if row is None:
                return False, "User not found or device mismatch", {}
            if row["status"] != "active":
                return False, "Account is not active", {}
            if row["locked_until"] is not None and row["locked_until"].timestamp() > time.time():
                return False, "Account is temporarily locked", {}

            # 更新登录时间与设备活跃时间
            cur.execute(
                sql.SQL("UPDATE accounts SET last_login_at = now() WHERE user_id = %s"),
                (user_id,),
            )
            cur.execute(
                sql.SQL("UPDATE devices SET last_seen_at = now() WHERE device_id = %s"),
                (device_id_hex,),
            )
        return True, "", dict(row)

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_hex(s: str) -> bool:
        try:
            bytes.fromhex(s)
            return True
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _dict_cursor():
        from psycopg2.extras import RealDictCursor
        return RealDictCursor

"""持久层抽象：`PostgresStore` / `MemoryStore`。

上层（`users.py` / `game.py`）只对着 ``Store`` 接口说话，不碰 SQL，好处：

* 本地调试和自动化测试可以 ``DB_BACKEND=memory`` 跑完整链路，不需要数据库；
* 将来换存储（SQLite / Redis / 分库…）只新增一个实现；
* SQL 集中一处，UniqueViolation 这类容易写错的细节只需对一次。

表结构见 `schema.sql`。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable

try:  # psycopg2 只在 postgres 后端才需要，memory 后端不要求装
    from psycopg2.errors import UniqueViolation
    _HAS_PSYCOPG2 = True
except Exception:  # pragma: no cover - 仅在生产依赖缺失时走到
    UniqueViolation = ()  # type: ignore[assignment]
    _HAS_PSYCOPG2 = False


class StoreError(Exception):
    """存储层错误基类。"""


class StoreConflict(StoreError):
    """唯一约束冲突。``field`` 为 ``user_id`` / ``user_name`` / ``device_id``。"""

    def __init__(self, field: str):
        self.field = field
        super().__init__(f"duplicate {field}")


class StoreUnavailable(StoreError):
    """存储不可用（连接断了、库没起来）。"""


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------

class Store:
    """存储接口。所有方法都必须线程安全。"""

    backend_name = "base"

    # --- 设备 -----------------------------------------------------------
    def register_device(
        self,
        device_id_hex: str,
        client_version: str = "",
        platform: str = "",
        ip: str = "",
    ) -> None:
        raise NotImplementedError

    def account_by_device(self, device_id_hex: str) -> dict | None:
        raise NotImplementedError

    # --- 账号 -----------------------------------------------------------
    def create_account(self, user_id: str, user_name: str, device_id_hex: str) -> None:
        """建账号；冲突时抛 ``StoreConflict``（带 field）。"""
        raise NotImplementedError

    def account_for_login(self, user_id: str, device_id_hex: str) -> dict | None:
        raise NotImplementedError

    def touch_activity(self, user_id: str, device_id_hex: str) -> None:
        raise NotImplementedError

    # --- 世界 / 玩家 ---------------------------------------------------
    def load_world(self) -> list[dict]:
        raise NotImplementedError

    def save_snapshot(self, items: Iterable[dict], players: Iterable[dict]) -> None:
        raise NotImplementedError

    def mark_all_offline(self) -> None:
        raise NotImplementedError

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

class PostgresStore(Store):
    backend_name = "postgres"

    def __init__(self, database: Any) -> None:
        self.db = database

    # --- 设备 -----------------------------------------------------------
    def register_device(
        self,
        device_id_hex: str,
        client_version: str = "",
        platform: str = "",
        ip: str = "",
    ) -> None:
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO devices (device_id, client_version, platform, last_ip)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (device_id) DO UPDATE SET
                    last_seen_at = now(),
                    client_version = COALESCE(NULLIF(EXCLUDED.client_version, ''), devices.client_version),
                    platform = COALESCE(NULLIF(EXCLUDED.platform, ''), devices.platform),
                    last_ip = COALESCE(NULLIF(EXCLUDED.last_ip, ''), devices.last_ip)
                """,
                (device_id_hex, client_version, platform, ip),
            )

    def account_by_device(self, device_id_hex: str) -> dict | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT user_id, user_name, status FROM accounts "
                "WHERE device_id = %s AND status = 'active'",
                (device_id_hex,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    # --- 账号 -----------------------------------------------------------
    def create_account(self, user_id: str, user_name: str, device_id_hex: str) -> None:
        """建账号。

        用 SAVEPOINT 而不是就地捕异常：一旦语句报错，整个事务就处于 aborted 状态，
        直接走到 commit 也会被 PostgreSQL 当成 ROLLBACK（旧版就丢在这里，
        连同一个事务里的 ``INSERT INTO devices`` 也一起没了）。
        """
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute("SAVEPOINT create_account")
            try:
                cur.execute(
                    "INSERT INTO accounts (user_id, device_id, user_name) "
                    "VALUES (%s, %s, %s)",
                    (user_id, device_id_hex, user_name),
                )
            except UniqueViolation as exc:
                cur.execute("ROLLBACK TO SAVEPOINT create_account")
                raise StoreConflict(_conflict_field(exc)) from exc
            cur.execute("RELEASE SAVEPOINT create_account")

    def account_for_login(self, user_id: str, device_id_hex: str) -> dict | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT user_id, user_name, device_id, status, locked_until, "
                "last_login_at "
                "FROM accounts WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def touch_activity(self, user_id: str, device_id_hex: str) -> None:
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET last_login_at = now() WHERE user_id = %s",
                (user_id,),
            )
            cur.execute(
                "UPDATE devices SET last_seen_at = now() WHERE device_id = %s",
                (device_id_hex,),
            )

    # --- 世界 / 玩家 ---------------------------------------------------
    def load_world(self) -> list[dict]:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT id, kind, pos_x, pos_y, pos_z, yaw, state FROM world_items"
            )
            return [dict(row) for row in cur.fetchall()]

    def save_snapshot(self, items: Iterable[dict], players: Iterable[dict]) -> None:
        with self.db.conn() as conn, conn.cursor() as cur:
            for it in items:
                cur.execute(
                    """
                    INSERT INTO world_items (id, kind, pos_x, pos_y, pos_z, yaw, state, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE SET
                        kind = EXCLUDED.kind,
                        pos_x = EXCLUDED.pos_x,
                        pos_y = EXCLUDED.pos_y,
                        pos_z = EXCLUDED.pos_z,
                        yaw = EXCLUDED.yaw,
                        state = EXCLUDED.state,
                        updated_at = now()
                    """,
                    (
                        it.get("id"),
                        it.get("kind", "generic"),
                        float(it.get("pos_x", 0.0)),
                        float(it.get("pos_y", 0.0)),
                        float(it.get("pos_z", 0.0)),
                        float(it.get("yaw", 0.0)),
                        str(it.get("state", "idle")),
                    ),
                )
            for p in players:
                cur.execute(
                    """
                    INSERT INTO players
                        (user_id, device_id, pos_x, pos_y, pos_z, yaw, hp, state, online, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (user_id) DO UPDATE SET
                        device_id = EXCLUDED.device_id,
                        pos_x = EXCLUDED.pos_x,
                        pos_y = EXCLUDED.pos_y,
                        pos_z = EXCLUDED.pos_z,
                        yaw = EXCLUDED.yaw,
                        hp = EXCLUDED.hp,
                        state = EXCLUDED.state,
                        online = EXCLUDED.online,
                        updated_at = now()
                    """,
                    (
                        p.get("user_id"),
                        p.get("device_id", ""),
                        float(p.get("pos_x", 0.0)),
                        float(p.get("pos_y", 0.0)),
                        float(p.get("pos_z", 0.0)),
                        float(p.get("yaw", 0.0)),
                        float(p.get("hp", 100.0)),
                        str(p.get("state", "idle")),
                        bool(p.get("online", False)),
                    ),
                )

    def mark_all_offline(self) -> None:
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE players SET online = FALSE WHERE online = TRUE")

    def ping(self) -> bool:
        return bool(self.db.ping())

    def close(self) -> None:
        self.db.close()


def _conflict_field(exc: Exception) -> str:
    """从 UniqueViolation 里推出是哪个字段重了。"""
    name = ""
    diag = getattr(exc, "diag", None)
    if diag is not None:
        name = getattr(diag, "constraint_name", "") or ""
    if not name:
        return "user_name"
    if name.endswith("_pkey"):
        return "user_id"
    if "user_name" in name:
        return "user_name"
    if "device_id" in name:
        return "device_id"
    return "user_name"


# ---------------------------------------------------------------------------
# 内存（本地调试 / 测试）
# ---------------------------------------------------------------------------

class MemoryStore(Store):
    """零依赖内存实现：进程退出就没了，但接口行为与 PostgresStore 一致。"""

    backend_name = "memory"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._devices: dict[str, dict] = {}
        self._accounts: dict[str, dict] = {}
        self._world: dict[str, dict] = {}
        self._players: dict[str, dict] = {}

    # --- 设备 -----------------------------------------------------------
    def register_device(
        self,
        device_id_hex: str,
        client_version: str = "",
        platform: str = "",
        ip: str = "",
    ) -> None:
        with self._lock:
            row = self._devices.setdefault(
                device_id_hex,
                {"device_id": device_id_hex, "created_at": time.time()},
            )
            row["last_seen_at"] = time.time()
            if client_version:
                row["client_version"] = client_version
            if platform:
                row["platform"] = platform
            if ip:
                row["last_ip"] = ip

    def account_by_device(self, device_id_hex: str) -> dict | None:
        with self._lock:
            for acc in self._accounts.values():
                if acc["device_id"] == device_id_hex and acc["status"] == "active":
                    return dict(acc)
        return None

    # --- 账号 -----------------------------------------------------------
    def create_account(self, user_id: str, user_name: str, device_id_hex: str) -> None:
        with self._lock:
            if user_id in self._accounts:
                raise StoreConflict("user_id")
            if any(a["user_name"] == user_name for a in self._accounts.values()):
                raise StoreConflict("user_name")
            if any(a["device_id"] == device_id_hex for a in self._accounts.values()):
                raise StoreConflict("device_id")
            self._accounts[user_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "device_id": device_id_hex,
                "status": "active",
                "locked_until": None,
                "created_at": time.time(),
                "last_login_at": None,
            }

    def account_for_login(self, user_id: str, device_id_hex: str) -> dict | None:
        with self._lock:
            acc = self._accounts.get(user_id)
            return dict(acc) if acc else None

    def touch_activity(self, user_id: str, device_id_hex: str) -> None:
        with self._lock:
            acc = self._accounts.get(user_id)
            if acc is not None:
                acc["last_login_at"] = time.time()
            dev = self._devices.get(device_id_hex)
            if dev is not None:
                dev["last_seen_at"] = time.time()

    # --- 世界 / 玩家 ---------------------------------------------------
    def load_world(self) -> list[dict]:
        with self._lock:
            return [dict(it) for it in self._world.values()]

    def save_snapshot(self, items: Iterable[dict], players: Iterable[dict]) -> None:
        with self._lock:
            for it in items:
                self._world[it["id"]] = dict(it)
            for p in players:
                self._players[p["user_id"]] = dict(p)

    def mark_all_offline(self) -> None:
        with self._lock:
            for p in self._players.values():
                p["online"] = False

    def dump(self) -> dict:
        """测试用：导出全部内存状态。"""
        with self._lock:
            return {
                "devices": [dict(v) for v in self._devices.values()],
                "accounts": [dict(v) for v in self._accounts.values()],
                "world": [dict(v) for v in self._world.values()],
                "players": [dict(v) for v in self._players.values()],
            }


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def build_store(cfg) -> Store:
    """按配置创建存储实现。"""
    if cfg.storage_is_memory:
        return MemoryStore()
    if not _HAS_PSYCOPG2:
        raise StoreUnavailable("postgres 后端需要 psycopg2-binary，请先 uv sync")
    from db import Database

    database = Database(
        dsn=cfg.database_url,
        minconn=cfg.db_pool_min,
        maxconn=cfg.db_pool_max,
    )
    return PostgresStore(database)

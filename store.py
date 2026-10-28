"""持久层抽象：`PostgresStore` / `MemoryStore`。

**数据库是唯一权威。** 上层（`users.py` / `game.py`）只对着 ``Store`` 接口说话，
不碰 SQL。这样做的三条好处：

* ``DB_BACKEND=memory`` 时能完整跑链路（本地调试 / 自动化测试），无需数据库；
* 将来换存储（SQLite / Redis / 分库…）只新增一个实现；
* “只有领先数据能进库”这条规则只写在 SQL 里一处，不会被上层绕过去。

写入门禁（重要）
----------------
`player` / `world` / `world_item` 三张表都带 ``rev`` 列，写入一律是::

    INSERT ... ON CONFLICT (id) DO UPDATE SET ... WHERE <表>.rev < EXCLUDED.rev

返回 ``{"written": n, "stale": [...]}``：

* ``written``：真正写进去的行数（含首次 INSERT）；
* ``stale``：因为库里 rev 更大而被**丢弃**的 id 列表。

上层必须把 ``stale`` 打进日志，否则“内存想盖掉库”这件事就看不见了。

表结构见 `schema.sql`（库在远端，由 `db.py` 连上时自动建）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable, Sequence

try:  # psycopg2 只在 postgres 后端才需要，memory 后端不要求装
    from psycopg2.errors import UniqueViolation

    _HAS_PSYCOPG2 = True
except Exception:  # pragma: no cover - 仅在生产依赖缺失时走到
    UniqueViolation = ()  # type: ignore[assignment]
    _HAS_PSYCOPG2 = False

log = logging.getLogger(__name__)

## 本期只有一世界；将来多世界时由客户端在请求里带 world_id
DEFAULT_WORLD = "default"

## player 表参与落库的字段（顺序与 INSERT 语句一致）
PLAYER_COLUMNS = (
    "user_id", "player_id", "file_hash", "player_name", "race",
    "pos_x", "pos_y", "pos_z", "yaw", "state", "online",
    "hp_max", "hp", "stamina_max", "stamina", "mana_max", "mana",
    "rev",
)
## world_item 表参与落库的字段
ITEM_COLUMNS = (
    "id", "world_id", "kind",
    "pos_x", "pos_y", "pos_z", "yaw", "state", "rev",
)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

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
# 值归一（两个实现共用，保证行为一致）
# ---------------------------------------------------------------------------

def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out or out in (float("inf"), float("-inf")):   # NaN / inf
        return default
    return out


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "t")


def player_params(row: dict) -> tuple:
    """把内存里的 player 行整理成 INSERT 参数（顺序 = PLAYER_COLUMNS）。"""
    return (
        _text(row.get("user_id")),
        _text(row.get("player_id")),
        _text(row.get("file_hash")),
        _text(row.get("player_name")),
        _text(row.get("race"), "human") or "human",
        _num(row.get("pos_x")),
        _num(row.get("pos_y")),
        _num(row.get("pos_z")),
        _num(row.get("yaw")),
        _text(row.get("state"), "idle") or "idle",
        _bool(row.get("online")),
        _num(row.get("hp_max"), 100.0),
        _num(row.get("hp"), 100.0),
        _num(row.get("stamina_max"), 100.0),
        _num(row.get("stamina"), 100.0),
        _num(row.get("mana_max"), 100.0),
        _num(row.get("mana"), 100.0),
        _int(row.get("rev")),
    )


def item_params(row: dict) -> tuple:
    """把内存里的 world_item 行整理成 INSERT 参数（顺序 = ITEM_COLUMNS）。"""
    return (
        _text(row.get("id")),
        _text(row.get("world_id"), DEFAULT_WORLD) or DEFAULT_WORLD,
        _text(row.get("kind"), "generic") or "generic",
        _num(row.get("pos_x")),
        _num(row.get("pos_y")),
        _num(row.get("pos_z")),
        _num(row.get("yaw")),
        _text(row.get("state"), "idle") or "idle",
        _int(row.get("rev")),
    )


def world_params(row: dict) -> tuple:
    return (
        _text(row.get("id"), DEFAULT_WORLD) or DEFAULT_WORLD,
        _text(row.get("name")),
        _int(row.get("seed")),
        _int(row.get("rev")),
    )


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------

class Store:
    """存储接口。所有方法都必须线程安全。"""

    backend_name = "base"

    # --- users：设备 / 账号 -------------------------------------------
    def touch_device(
        self,
        device_id_hex: str,
        client_version: str = "",
        platform: str = "",
        ip: str = "",
    ) -> None:
        """登记/刷新一个设备（未注册的设备 user_id 为 NULL）。"""
        raise NotImplementedError

    def account_by_device(self, device_id_hex: str) -> dict | None:
        """按设备取已绑定且可用的账号；未绑返回 None。"""
        raise NotImplementedError

    def create_user(self, user_id: str, user_name: str, device_id_hex: str) -> None:
        """把账号绑到设备上；冲突抛 ``StoreConflict``（带 field）。"""
        raise NotImplementedError

    def user_for_login(self, user_id: str) -> dict | None:
        """登录用：按 user_id 取整行（含 device_id / status / locked_until）。"""
        raise NotImplementedError

    def name_exists(self, user_name: str) -> bool:
        """账号名是否已被占用（注册前的重名检测用）。"""
        raise NotImplementedError

    def player_file_hash(self, user_id: str) -> str:
        """取 player 行里记录的登录校验文件哈希；无行 / 未初始化返回空串。"""
        raise NotImplementedError

    def set_player_file_hash(self, user_id: str, file_hash: str) -> None:
        """首次登录时写入文件哈希（已有值不覆盖；无行则补行）。"""
        raise NotImplementedError

    def touch_login(self, user_id: str, device_id_hex: str) -> None:
        raise NotImplementedError

    # --- player ---------------------------------------------------------
    def load_players(self) -> list[dict]:
        """读全量玩家行（附带 users.user_name）。"""
        raise NotImplementedError

    def load_player(self, user_id: str) -> dict | None:
        """读单名玩家行（下线销毁后重连时重载用）。"""
        raise NotImplementedError

    def save_players(self, rows: Iterable[dict]) -> dict:
        """批量落库；返回 ``{"written": n, "stale": [...]}``。"""
        raise NotImplementedError

    def mark_all_offline(self) -> None:
        raise NotImplementedError

    # --- world / world_item --------------------------------------------
    def load_world(self, world_id: str = DEFAULT_WORLD) -> dict:
        """返回 ``{"world": row|None, "items": [row, ...]}``。"""
        raise NotImplementedError

    def save_world(
        self, world_row: dict | None = None, item_rows: Iterable[dict] = (),
    ) -> dict:
        """落库世界元信息 + 实体；返回 ``{"written": n, "stale": [...]}``。"""
        raise NotImplementedError

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

## 只有库里 rev 更小才允许覆盖（= 只接受领先数据）
_SAVE_PLAYER_SQL = """
    INSERT INTO player (
        user_id, player_id, file_hash, player_name, race,
        pos_x, pos_y, pos_z, yaw, state, online,
        hp_max, hp, stamina_max, stamina, mana_max, mana, rev, updated_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (user_id) DO UPDATE SET
        player_id     = COALESCE(NULLIF(EXCLUDED.player_id, ''), player.player_id),
        file_hash     = COALESCE(NULLIF(EXCLUDED.file_hash, ''), player.file_hash),
        player_name   = EXCLUDED.player_name,
        race          = EXCLUDED.race,
        pos_x         = EXCLUDED.pos_x,
        pos_y         = EXCLUDED.pos_y,
        pos_z         = EXCLUDED.pos_z,
        yaw           = EXCLUDED.yaw,
        state         = EXCLUDED.state,
        online        = EXCLUDED.online,
        hp_max        = EXCLUDED.hp_max,
        hp            = EXCLUDED.hp,
        stamina_max   = EXCLUDED.stamina_max,
        stamina       = EXCLUDED.stamina,
        mana_max      = EXCLUDED.mana_max,
        mana          = EXCLUDED.mana,
        rev           = EXCLUDED.rev,
        updated_at    = now()
    WHERE player.rev < EXCLUDED.rev
"""

_SAVE_WORLD_SQL = """
    INSERT INTO world (id, name, seed, rev, updated_at)
    VALUES (%s, %s, %s, %s, now())
    ON CONFLICT (id) DO UPDATE SET
        name       = EXCLUDED.name,
        seed       = EXCLUDED.seed,
        rev        = EXCLUDED.rev,
        updated_at = now()
    WHERE world.rev < EXCLUDED.rev
"""

_SAVE_ITEM_SQL = """
    INSERT INTO world_item (
        id, world_id, kind, pos_x, pos_y, pos_z, yaw, state, rev, updated_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (id) DO UPDATE SET
        world_id   = EXCLUDED.world_id,
        kind       = EXCLUDED.kind,
        pos_x      = EXCLUDED.pos_x,
        pos_y      = EXCLUDED.pos_y,
        pos_z      = EXCLUDED.pos_z,
        yaw        = EXCLUDED.yaw,
        state      = EXCLUDED.state,
        rev        = EXCLUDED.rev,
        updated_at = now()
    WHERE world_item.rev < EXCLUDED.rev
"""

_CREATE_USER_SQL = """
    INSERT INTO users (device_id, user_id, user_name)
    VALUES (%s, %s, %s)
    ON CONFLICT (device_id) DO UPDATE SET
        user_id      = EXCLUDED.user_id,
        user_name    = EXCLUDED.user_name,
        last_seen_at = now()
    WHERE users.user_id IS NULL
    RETURNING user_id
"""


class PostgresStore(Store):
    backend_name = "postgres"

    def __init__(self, database: Any) -> None:
        self.db = database

    # --- users ----------------------------------------------------------
    def touch_device(
        self,
        device_id_hex: str,
        client_version: str = "",
        platform: str = "",
        ip: str = "",
    ) -> None:
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (device_id, client_version, platform, last_ip)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (device_id) DO UPDATE SET
                    last_seen_at   = now(),
                    client_version = COALESCE(NULLIF(EXCLUDED.client_version, ''), users.client_version),
                    platform       = COALESCE(NULLIF(EXCLUDED.platform, ''), users.platform),
                    last_ip        = COALESCE(NULLIF(EXCLUDED.last_ip, ''), users.last_ip)
                """,
                (device_id_hex, client_version, platform, ip),
            )

    def account_by_device(self, device_id_hex: str) -> dict | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT user_id, user_name, status, locked_until FROM users "
                "WHERE device_id = %s AND user_id IS NOT NULL AND status = 'active'",
                (device_id_hex,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def create_user(self, user_id: str, user_name: str, device_id_hex: str) -> None:
        """把账号绑到设备上。

        三种失败都要分清：

        * 设备已绑（``users.user_id`` 已非 NULL）—— ``ON CONFLICT ... WHERE`` 命中 0 行，
          靠 ``RETURNING`` 为空判定，**不是异常**；
        * ``user_id`` / ``user_name`` 重——真抛 ``UniqueViolation``。

        用 SAVEPOINT 而不是就地捕异常：一旦语句报错，整个事务就进入 aborted 状态，
        直接走到 commit 也会被 PostgreSQL 当成 ROLLBACK（旧版就丢在这里）。
        """
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute("SAVEPOINT create_user")
            try:
                cur.execute(_CREATE_USER_SQL, (device_id_hex, user_id, user_name))
                row = cur.fetchone()
            except UniqueViolation as exc:
                cur.execute("ROLLBACK TO SAVEPOINT create_user")
                raise StoreConflict(_conflict_field(exc)) from exc
            cur.execute("RELEASE SAVEPOINT create_user")
            if row is None:
                raise StoreConflict("device_id")

    def user_for_login(self, user_id: str) -> dict | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT user_id, user_name, device_id, status, locked_until, last_login_at "
                "FROM users WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def touch_login(self, user_id: str, device_id_hex: str) -> None:
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET last_login_at = now(), last_seen_at = now() "
                "WHERE user_id = %s",
                (user_id,),
            )
            cur.execute(
                "UPDATE users SET last_seen_at = now() WHERE device_id = %s",
                (device_id_hex,),
            )

    def name_exists(self, user_name: str) -> bool:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM users WHERE user_name = %s LIMIT 1",
                (user_name,),
            )
            return cur.fetchone() is not None

    def player_file_hash(self, user_id: str) -> str:
        with self.db.cursor() as cur:
            cur.execute("SELECT file_hash FROM player WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
        return _text((row or {}).get("file_hash")) if row else ""

    def set_player_file_hash(self, user_id: str, file_hash: str) -> None:
        """只在库中仍为空时写：避免把上一个客户端的哈希刷掉。

        用 upsert 而不是 UPDATE：首次登录时 player 行可能还没落库
        （注册后只在内存热缓存里，要等定时落库），否则这里会空转。
        """
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO player (user_id, file_hash, rev)
                VALUES (%s, %s, 0)
                ON CONFLICT (user_id) DO UPDATE SET
                    file_hash  = EXCLUDED.file_hash,
                    updated_at = now()
                WHERE player.file_hash = ''
                """,
                (user_id, file_hash),
            )

    # --- player ---------------------------------------------------------
    def load_players(self) -> list[dict]:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT p.*, u.user_name AS account_name "
                "FROM player p LEFT JOIN users u ON u.user_id = p.user_id"
            )
            rows = [dict(row) for row in cur.fetchall()]
        for row in rows:
            row["user_name"] = row.pop("account_name", "") or ""
        return rows

    def load_player(self, user_id: str) -> dict | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT p.*, u.user_name AS account_name "
                "FROM player p LEFT JOIN users u ON u.user_id = p.user_id "
                "WHERE p.user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        out = dict(row)
        out["user_name"] = out.pop("account_name", "") or ""
        return out

    def save_players(self, rows: Iterable[dict]) -> dict:
        pending: Sequence[dict] = [r for r in rows if _text(r.get("user_id"))]
        written = 0
        stale: list[str] = []
        if not pending:
            return {"written": 0, "stale": stale}
        with self.db.conn() as conn, conn.cursor() as cur:
            for row in pending:
                cur.execute(_SAVE_PLAYER_SQL, player_params(row))
                if cur.rowcount:
                    written += 1
                else:
                    stale.append(_text(row.get("user_id")))
        return {"written": written, "stale": stale}

    def mark_all_offline(self) -> None:
        with self.db.conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE player SET online = FALSE WHERE online = TRUE")

    # --- world ----------------------------------------------------------
    def load_world(self, world_id: str = DEFAULT_WORLD) -> dict:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT id, name, seed, rev, created_at, updated_at "
                "FROM world WHERE id = %s",
                (world_id,),
            )
            world_row = cur.fetchone()
            cur.execute(
                "SELECT id, world_id, kind, pos_x, pos_y, pos_z, yaw, state, rev, updated_at "
                "FROM world_item WHERE world_id = %s",
                (world_id,),
            )
            items = [dict(row) for row in cur.fetchall()]
        return {"world": dict(world_row) if world_row else None, "items": items}

    def save_world(self, world_row: dict | None = None, item_rows: Iterable[dict] = ()) -> dict:
        written = 0
        stale: list[str] = []
        items: Sequence[dict] = [i for i in item_rows if _text(i.get("id"))]
        with self.db.conn() as conn, conn.cursor() as cur:
            if world_row and _text(world_row.get("id")):
                cur.execute(_SAVE_WORLD_SQL, world_params(world_row))
                if cur.rowcount:
                    written += 1
                else:
                    stale.append(_text(world_row.get("id")))
            for item in items:
                cur.execute(_SAVE_ITEM_SQL, item_params(item))
                if cur.rowcount:
                    written += 1
                else:
                    stale.append(_text(item.get("id")))
        return {"written": written, "stale": stale}

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
        return "device_id"      # users 的主键是 device_id
    if "user_id" in name:
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
    """零依赖内存实现：进程退出就没了，但接口行为与 PostgresStore 一致。

    包括 rev 门禁：它也必须拒绝落后数据，否则上层代码在两份后端上行为不同。
    """

    backend_name = "memory"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: dict[str, dict] = {}        # device_id -> users 行
        self._players: dict[str, dict] = {}      # user_id  -> player 行
        self._world: dict[str, dict] = {}        # world_id -> world 行
        self._items: dict[str, dict] = {}        # item_id  -> world_item 行
        self._world.setdefault(DEFAULT_WORLD, {
            "id": DEFAULT_WORLD, "name": "众生之门", "seed": 0, "rev": 0,
        })

    # --- users ----------------------------------------------------------
    def touch_device(
        self,
        device_id_hex: str,
        client_version: str = "",
        platform: str = "",
        ip: str = "",
    ) -> None:
        with self._lock:
            row = self._users.setdefault(
                device_id_hex,
                {
                    "device_id": device_id_hex,
                    "user_id": None,
                    "user_name": None,
                    "status": "active",
                    "locked_until": None,
                    "client_version": "",
                    "platform": "",
                    "last_ip": "",
                    "created_at": time.time(),
                },
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
            row = self._users.get(device_id_hex)
            if not row or not row.get("user_id") or row.get("status") != "active":
                return None
            return {
                "user_id": row["user_id"],
                "user_name": row.get("user_name") or "",
                "status": row.get("status", "active"),
                "locked_until": row.get("locked_until"),
            }

    def create_user(self, user_id: str, user_name: str, device_id_hex: str) -> None:
        with self._lock:
            current = self._users.get(device_id_hex)
            if current is not None and current.get("user_id"):
                raise StoreConflict("device_id")
            if any(u.get("user_id") == user_id for u in self._users.values()):
                raise StoreConflict("user_id")
            if any(u.get("user_name") == user_name for u in self._users.values()):
                raise StoreConflict("user_name")
            row = current or {
                "device_id": device_id_hex,
                "status": "active",
                "locked_until": None,
                "created_at": time.time(),
            }
            row["user_id"] = user_id
            row["user_name"] = user_name
            row["last_seen_at"] = time.time()
            self._users[device_id_hex] = row

    def user_for_login(self, user_id: str) -> dict | None:
        with self._lock:
            for row in self._users.values():
                if row.get("user_id") == user_id:
                    return dict(row)
        return None

    def touch_login(self, user_id: str, device_id_hex: str) -> None:
        with self._lock:
            for row in self._users.values():
                if row.get("user_id") == user_id:
                    row["last_login_at"] = time.time()
                    row["last_seen_at"] = time.time()
                    break
            dev = self._users.get(device_id_hex)
            if dev is not None:
                dev["last_seen_at"] = time.time()

    def name_exists(self, user_name: str) -> bool:
        with self._lock:
            return any(u.get("user_name") == user_name for u in self._users.values())

    def player_file_hash(self, user_id: str) -> str:
        with self._lock:
            row = self._players.get(user_id)
            return _text((row or {}).get("file_hash"))

    def set_player_file_hash(self, user_id: str, file_hash: str) -> None:
        """同 PostgresStore：库中已有非空值则不覆盖，无行则补一行。"""
        with self._lock:
            row = self._players.get(user_id)
            if row is None:
                self._players[user_id] = {"user_id": user_id, "file_hash": _text(file_hash), "rev": 0}
            elif not _text(row.get("file_hash")):
                row["file_hash"] = _text(file_hash)

    # --- player ---------------------------------------------------------
    def load_players(self) -> list[dict]:
        names = {row.get("user_id"): row.get("user_name") or ""
                 for row in self._users.values() if row.get("user_id")}
        with self._lock:
            out = []
            for row in self._players.values():
                item = dict(row)
                item["user_name"] = names.get(item.get("user_id"), "")
                out.append(item)
            return out

    def load_player(self, user_id: str) -> dict | None:
        with self._lock:
            row = self._players.get(user_id)
            if row is None:
                return None
            out = dict(row)
        for u in self._users.values():
            if u.get("user_id") == user_id:
                out["user_name"] = u.get("user_name") or ""
                break
        else:
            out.setdefault("user_name", "")
        return out

    def save_players(self, rows: Iterable[dict]) -> dict:
        written = 0
        stale: list[str] = []
        with self._lock:
            for row in rows:
                user_id = _text(row.get("user_id"))
                if not user_id:
                    continue
                current = self._players.get(user_id)
                if current is not None and _int(current.get("rev")) >= _int(row.get("rev")):
                    stale.append(user_id)
                    continue
                stored = dict(row)
                stored.pop("user_name", None)   # 账号名归 users 表管
                stored["user_id"] = user_id
                # 与 PostgresStore 的 COALESCE(NULLIF(...)) 同构：
                # 内存行没带 player_id / file_hash 时不能把库里已有的刷空
                for keep in ("player_id", "file_hash"):
                    if not _text(stored.get(keep)) and current is not None:
                        stored[keep] = _text(current.get(keep))
                self._players[user_id] = stored
                written += 1
        return {"written": written, "stale": stale}

    def mark_all_offline(self) -> None:
        with self._lock:
            for row in self._players.values():
                row["online"] = False

    # --- world ----------------------------------------------------------
    def load_world(self, world_id: str = DEFAULT_WORLD) -> dict:
        with self._lock:
            world_row = self._world.get(world_id)
            items = [dict(it) for it in self._items.values()
                     if it.get("world_id") == world_id]
            return {"world": dict(world_row) if world_row else None, "items": items}

    def save_world(self, world_row: dict | None = None, item_rows: Iterable[dict] = ()) -> dict:
        written = 0
        stale: list[str] = []
        with self._lock:
            if world_row and _text(world_row.get("id")):
                world_id = _text(world_row.get("id"))
                current = self._world.get(world_id)
                if current is not None and _int(current.get("rev")) >= _int(world_row.get("rev")):
                    stale.append(world_id)
                else:
                    self._world[world_id] = dict(world_row)
                    written += 1
            for item in item_rows:
                item_id = _text(item.get("id"))
                if not item_id:
                    continue
                current = self._items.get(item_id)
                if current is not None and _int(current.get("rev")) >= _int(item.get("rev")):
                    stale.append(item_id)
                    continue
                self._items[item_id] = dict(item)
                written += 1
        return {"written": written, "stale": stale}

    def dump(self) -> dict:
        """测试用：导出全部内存状态。"""
        with self._lock:
            return {
                "users": [dict(v) for v in self._users.values()],
                "player": [dict(v) for v in self._players.values()],
                "world": [dict(v) for v in self._world.values()],
                "world_item": [dict(v) for v in self._items.values()],
            }


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def build_store(cfg) -> Store:
    """按配置创建存储实现（postgres 会连 .env 里的远端库并自动建表）。"""
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

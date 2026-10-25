-- 众生之门 账号 / 设备绑定 表结构
-- 幂等：可重复执行

CREATE TABLE IF NOT EXISTS devices (
    device_id      TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'active',
    client_version TEXT NOT NULL DEFAULT '',   -- 最近一次上报的客户端版本
    platform       TEXT NOT NULL DEFAULT '',   -- windows / linux / android …
    last_ip        TEXT NOT NULL DEFAULT '',   -- 最近一次连接来源 IP
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 旧库升级：这三个列是 v1.1 才加的，可重复执行
ALTER TABLE devices ADD COLUMN IF NOT EXISTS client_version TEXT NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN IF NOT EXISTS platform       TEXT NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN IF NOT EXISTS last_ip        TEXT NOT NULL DEFAULT '';

-- 一个设备只能绑定一个账号（device_id UNIQUE）
-- 一个账号永久绑定首次登记的设备（本表不做设备迁移）
CREATE TABLE IF NOT EXISTS accounts (
    user_id       TEXT PRIMARY KEY,
    device_id     TEXT NOT NULL UNIQUE REFERENCES devices (device_id) ON DELETE CASCADE,
    user_name     TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL DEFAULT 'active',
    locked_until  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS accounts_device_id_idx ON accounts (device_id);
CREATE INDEX IF NOT EXISTS accounts_user_name_idx ON accounts (user_name);

-- ---------------------------------------------------------------------------
-- 世界物品表：世界中可移动物品的权威位置
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS world_items (
    id         TEXT PRIMARY KEY,                            -- 例：door_01 / crate_07
    kind       TEXT NOT NULL DEFAULT 'generic',             -- 物品类型（门 / 箱子 / 可推动物件…）
    pos_x      DOUBLE PRECISION NOT NULL DEFAULT 0,         -- 世界坐标 x
    pos_y      DOUBLE PRECISION NOT NULL DEFAULT 0,         -- 世界坐标 y
    pos_z      DOUBLE PRECISION NOT NULL DEFAULT 0,         -- 世界坐标 z
    yaw        DOUBLE PRECISION NOT NULL DEFAULT 0,         -- 面向角度（度，0 = 正北 / -Z）
    state      TEXT NOT NULL DEFAULT 'idle',                -- idle / moving / open …
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 玩家表：玩家档案与最后已知位置（定时落库）
-- 启动时所有行会被置为 online = FALSE，因为进程重启后无人真正在线。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    user_id      TEXT PRIMARY KEY REFERENCES accounts (user_id) ON DELETE CASCADE,
    device_id    TEXT NOT NULL REFERENCES devices (device_id) ON DELETE CASCADE,
    pos_x        DOUBLE PRECISION NOT NULL DEFAULT 0,       -- 最后已知世界坐标 x
    pos_y        DOUBLE PRECISION NOT NULL DEFAULT 0,
    pos_z        DOUBLE PRECISION NOT NULL DEFAULT 0,
    yaw          DOUBLE PRECISION NOT NULL DEFAULT 0,       -- 面向角度（度）
    hp           DOUBLE PRECISION NOT NULL DEFAULT 100,     -- 生命值
    state        TEXT NOT NULL DEFAULT 'idle',              -- idle / moving / dead …
    online       BOOLEAN NOT NULL DEFAULT FALSE,            -- 是否在线（落库时的快照值）
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS players_device_id_idx ON players (device_id);


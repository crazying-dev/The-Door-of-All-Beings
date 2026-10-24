-- 众生之门 账号 / 设备绑定 表结构
-- 幂等：可重复执行

CREATE TABLE IF NOT EXISTS devices (
    device_id    TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
-- 世界物品表：世界中可移动物品的权威位置（现在为空表，结构已定型）
-- 玩家位置不落库：进程重启后无人在线，历史坐标没有意义。
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


-- 众生之门 —— 数据库结构（v2）
--
-- ===========================================================================
-- 三条铁律
-- ===========================================================================
-- 1. **数据库是唯一权威**。服务端内存只是一份热缓存：进程重启就丢掉，
--    重新从库里读回来；任何「内存和库不一致」都以库为准。
-- 2. **只有领先的数据才能进库**。player / world / world_item 三张表都带
--    ``rev`` 列，写入语句一律是 ``ON CONFLICT ... DO UPDATE ... WHERE <表>.rev < EXCLUDED.rev``：
--    库里的 rev 更大就直接丢弃这次写（调用方按 rowcount 统计 stale）。
--    这样即使一个卡住的旧线程晚到，也绝不可能把新数据盖回去。
-- 3. 幂等：本文件可以反复执行，不会报错、不会清数据（库在远端，启动时自动跑）。
--
-- ===========================================================================
-- 表一览
-- ===========================================================================
--   users       登录校验：设备 ID -> 玩家 ID（一个设备锁一个账号）
--   player      玩家数据：位置 / 状态 / 三围（主键 = 玩家 ID）
--   world       世界元信息（id / 名称 / 种子 / rev）
--   world_item  世界实体：门 / 箱子 / 杆……（外键 -> world）
--   admin       预留（见文件末尾注释，本期不建表）
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- 一，users：登录校验
--   设备 ID 是主键（设备第一次连上来就要能登记，那时还没有玩家 ID）；
--   玩家 ID 由服务端分配，未注册时为 NULL（UNIQUE 允许多个 NULL）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    device_id      TEXT PRIMARY KEY,                      -- 设备 ID（hex 字符串）
    user_id        TEXT UNIQUE,                           -- 玩家 ID（ZWME-2WKQ），未注册为 NULL
    user_name      TEXT UNIQUE,                           -- 账号名（注册时定，唯一）
    status         TEXT NOT NULL DEFAULT 'active',        -- active / disabled
    locked_until   TIMESTAMPTZ,                           -- 封禁到期时间，NULL = 未封
    client_version TEXT NOT NULL DEFAULT '',              -- 最近一次上报的客户端版本
    platform       TEXT NOT NULL DEFAULT '',              -- windows / linux / android …
    last_ip        TEXT NOT NULL DEFAULT '',              -- 最近一次连接来源 IP
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);

-- 老库升级用（可重复执行）；新库执行时是空操作
ALTER TABLE users ADD COLUMN IF NOT EXISTS status         TEXT NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until   TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS client_version TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS platform       TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_ip        TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at  TIMESTAMPTZ;


-- ---------------------------------------------------------------------------
-- 二，player：玩家数据
--   位置是「最后落库的位置」，不是实时的；实时位置在内存 + PUSH 里。
--   online 只是落库那一刻的快照值：服务启动时会被统一清成 FALSE，
--   因为进程重启后其实没有任何人在线。
--   三围（生命 / 体力 / 灵力）同时存上限和当前值，上限由种族成长决定、
--   当前值由战斗 / 消耗决定，二者必须分开存。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS player (
    user_id     TEXT PRIMARY KEY REFERENCES users (user_id) ON DELETE CASCADE,
    player_id   TEXT NOT NULL DEFAULT '',                 -- 客户端生成的 UUID（本地标识）
    file_hash   TEXT NOT NULL DEFAULT '',                 -- 登录校验文件哈希（首次登录时初始化）
    player_name TEXT NOT NULL DEFAULT '',                 -- 角色名（游戏内显示）
    race        TEXT NOT NULL DEFAULT 'human',            -- 种族
    pos_x       DOUBLE PRECISION NOT NULL DEFAULT 0,      -- 最后落库的世界坐标
    pos_y       DOUBLE PRECISION NOT NULL DEFAULT 0,
    pos_z       DOUBLE PRECISION NOT NULL DEFAULT 0,
    yaw         DOUBLE PRECISION NOT NULL DEFAULT 0,      -- 朝向（度，0 = 正北 / -Z）
    state       TEXT NOT NULL DEFAULT 'idle',             -- idle / moving / dead
    online      BOOLEAN NOT NULL DEFAULT FALSE,           -- 落库时的在线快照
    hp_max      DOUBLE PRECISION NOT NULL DEFAULT 100,    -- 生命上限
    hp          DOUBLE PRECISION NOT NULL DEFAULT 100,    -- 当前生命值
    stamina_max DOUBLE PRECISION NOT NULL DEFAULT 100,    -- 体力上限
    stamina     DOUBLE PRECISION NOT NULL DEFAULT 100,    -- 当前体力
    mana_max    DOUBLE PRECISION NOT NULL DEFAULT 100,    -- 灵力上限
    mana        DOUBLE PRECISION NOT NULL DEFAULT 100,    -- 当前灵力
    rev         BIGINT NOT NULL DEFAULT 0,                -- 修订号：只有更大才能覆盖
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE player ADD COLUMN IF NOT EXISTS player_name TEXT NOT NULL DEFAULT '';
ALTER TABLE player ADD COLUMN IF NOT EXISTS player_id   TEXT NOT NULL DEFAULT '';
ALTER TABLE player ADD COLUMN IF NOT EXISTS file_hash   TEXT NOT NULL DEFAULT '';
ALTER TABLE player ADD COLUMN IF NOT EXISTS race        TEXT NOT NULL DEFAULT 'human';
ALTER TABLE player ADD COLUMN IF NOT EXISTS stamina_max DOUBLE PRECISION NOT NULL DEFAULT 100;
ALTER TABLE player ADD COLUMN IF NOT EXISTS stamina     DOUBLE PRECISION NOT NULL DEFAULT 100;
ALTER TABLE player ADD COLUMN IF NOT EXISTS mana_max    DOUBLE PRECISION NOT NULL DEFAULT 100;
ALTER TABLE player ADD COLUMN IF NOT EXISTS mana        DOUBLE PRECISION NOT NULL DEFAULT 100;
ALTER TABLE player ADD COLUMN IF NOT EXISTS rev         BIGINT NOT NULL DEFAULT 0;


-- ---------------------------------------------------------------------------
-- 三，world：世界数据
--   world      一行 = 一个世界（元信息）；本期只有 'default' 一个。
--   world_item 一行 = 世界里一个可同步实体（门 / 箱子 / 杆 / 掉落物…）。
--   拆两张表是因为「世界的种子 / 名称」和「世界里东西的坐标」生命周期不同：
--   前者几乎不变，后者 10Hz 级变动，混在一张表里会让每次 upsert 都重写种子。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS world (
    id         TEXT PRIMARY KEY,                          -- 世界 ID，本期固定 'default'
    name       TEXT NOT NULL DEFAULT '',                  -- 世界名（客户端标题用）
    seed       BIGINT NOT NULL DEFAULT 0,                 -- 地形随机种子
    rev        BIGINT NOT NULL DEFAULT 0,                 -- 修订号：只有更大才能覆盖
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS world_item (
    id         TEXT PRIMARY KEY,                          -- 例：door_01 / crate_07
    world_id   TEXT NOT NULL DEFAULT 'default'
               REFERENCES world (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL DEFAULT 'generic',           -- door / crate / lever / generic …
    pos_x      DOUBLE PRECISION NOT NULL DEFAULT 0,
    pos_y      DOUBLE PRECISION NOT NULL DEFAULT 0,
    pos_z      DOUBLE PRECISION NOT NULL DEFAULT 0,
    yaw        DOUBLE PRECISION NOT NULL DEFAULT 0,       -- 面向角度（度）
    state      TEXT NOT NULL DEFAULT 'idle',              -- idle / moving / open / locked
    rev        BIGINT NOT NULL DEFAULT 0,                 -- 修订号：只有更大才能覆盖
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS world_item_world_id_idx ON world_item (world_id);

-- 默认世界必须存在：world_item.world_id 有外键，少了它第一个物品就插不进去
INSERT INTO world (id, name, seed) VALUES ('default', '众生之门', 0)
ON CONFLICT (id) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 四，admin：管理后台（**本期不建表**，仅记录设计意图）
-- ---------------------------------------------------------------------------
-- 建表时按下面的形状落（与 users 分表，不要塞进 users）：
--
--   CREATE TABLE admin (
--       admin_id     TEXT PRIMARY KEY,                 -- 形如 ADM-0001
--       user_name    TEXT NOT NULL UNIQUE,             -- 登录名
--       pass_hash    TEXT NOT NULL,                    -- 口令的慢哈希（scrypt / argon2）
--       role         TEXT NOT NULL DEFAULT 'viewer',   -- owner / gm / viewer
--       created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
--       last_login_at TIMESTAMPTZ
--   );
--
-- 与管理对象的关系（届时再加，不要现在猜）：
--   admin_action(admin_id, target_user_id, action, payload, created_at)  操作审计
--   admin_ban(target_user_id, until, reason, by_admin, created_at)       封禁

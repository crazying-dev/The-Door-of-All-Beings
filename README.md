# 众生之门 TCP 游戏服（The Door of All Beings · Socket Server）

TCP 游戏协议 + 账号 / 世界数据 + **数据库权威 + rev 门槛** + **四阶段启动（s1/s2/s3/s4）**。

HTTP 发现接口已拆到独立组件 **`server`**（仓库 `server` 分支），两边互不依赖、可分别部署与重启。

| 项 | 值 |
|---|---|
| 协议版本 | **1.3**（4 行竖线报文，长连接，会话 Token，**无主动推送（客户端拉取）**，头+正文联合哈希，支持批量多 action；兼容 v1.1/v1.2） |
| 服务版本 | **4.0.0** |
| TCP 游戏端口 | `TCP_PORT`（默认跟随 `PORT`，即 **5423**） |
| 存储 | PostgreSQL（生产）/ 内存（调试与测试） |
| 依赖 | Python 3.12 · psycopg2-binary · python-dotenv |

协议细节全部在 **[`protocol.md`](protocol.md)**（客户端对接看那一份就够），
业务流程（注册 / 登录 / 下线 / 宽限期重连）在 **[`flows.md`](flows.md)**。

---

## 1. 快速开始

### 1.1 本地跑起来（不接数据库）

```bash
cp .env.example .env      # Windows: copy .env.example .env
# .env 里写一行 DEVICE_ID_SECRET=<32+ 字节随机>
uv run main.py --backend memory
```

`--backend memory` 时数据只在内存，重启即清空；`DEVICE_ID_SECRET` 会临时生成（仅内存后端允许）。

### 1.2 生产跑起来（接 PostgreSQL）

```bash
cp .env.example .env
python -c "import secrets;print(secrets.token_hex(32))"   # 填进 DEVICE_ID_SECRET
uv run scripts/init_db.py      # 建表 / 升级（幂等）
uv run scripts/migrate_legacy.py --dry-run   # 看一眼旧表有多少行要搬
uv run scripts/migrate_legacy.py             # 旧表 devices/accounts → users/player（可逆）
uv run main.py check           # 自检：配置 / 存储 / 设备密钥 / 表结构
uv run main.py                 # 起 TCP 5423（HTTP 发现接口由 server 组件单独跑）
```

> ⚠️ `DEVICE_ID_SECRET` **定下来就不要改**。设备 ID 由它签名，
> 换了密钥所有老客户端存的 device_id 全部失效，老账号再也绑不上。

---

## 2. 命令行

```bash
uv run main.py serve     # TCP 游戏协议（默认）
uv run main.py tcp       # 同上（历史别名）
uv run main.py check     # 自检：配置 / 存储 / 设备密钥 / 动作表
uv run main.py version   # 打印服务与协议版本
```

常用参数：`--debug`（调试日志）、`--backend memory|postgres`。

---

## 3. 代码结构

```
server-socket/
├─ main.py          入口：Service 四阶段（s1 初始化 / s2 预热 / s3 启动 / s4 关闭）
├─ lifecycle.py     四阶段日志与关闭回调栈（LIFO）
├─ config.py        所有配置集中在这里（只从 .env / 环境变量读）
├─ protocol.py      协议 v1.3：解析 / 编码 / 错误码（头+正文联合哈希、批量多 action；第 3 行兼容 2/3 字段）
├─ server.py        TCP 监听（create_socket → bind → start）、帧读取、单连接线程、防重放、Token 校验
├─ session.py       长连接会话（Session）与会话表 + 会话 Token + 断线宽限期（v1.3：不再广播）
├─ actions.py       动作注册表（@action），业务入口
├─ users.py         账号业务：设备登记 / 注册 / 登录
├─ device_id.py     设备 ID 的签发与无状态校验（HMAC）
├─ game.py          热缓存：世界物品 + 玩家行（rev 逐行递增），增量落库
├─ store.py         存储抽象：PostgresStore / MemoryStore（rev 门槛在这里）
├─ anticheat.py     防外挂框架（目前 0 条规则，只占位不起线程）
├─ db.py            PostgreSQL 连接池 + 自动执行 schema.sql
├─ schema.sql       表结构（幂等，可重复执行）
├─ protocol.md      协议规范（客户端对接看这份）
├─ flows.md         业务流程规范（5 大流程与服务端动作顺序）
├─ deploy/          systemd 示例
└─ scripts/
   ├─ init_db.py         建表 / 升级
   ├─ migrate_legacy.py  旧表（devices/accounts/players/world_items）迁移，可 --dry-run
   ├─ tcp_smoke_test.py  协议冒烟测试（无需数据库）
   └─ store_check.py     PostgreSQL 存储层校验（跑完自清理）
```

---

## 4. 校验与测试

服务端没有引入 pytest：两个脚本都是零额外依赖，退出码 0 = 全绿。

```bash
# 协议全链路（内存后端，起真 TCP，不用数据库）
uv run scripts/tcp_smoke_test.py          # 当前 110/110
uv run scripts/tcp_smoke_test.py -v       # 逐条打印

# PostgreSQL 存储层（用真库，但只碰自己造的临时数据，跑完清理）
uv run scripts/store_check.py             # 当前 25/25

# 配自检（真实生产环境跑这条最合适）
uv run main.py check
```

`tcp_smoke_test.py` 覆盖：hello 下发 Token / 握手 / 联合哈希篡改（改头部即失效）/ v1.2 向后兼容 /
批量多 action / 设备签发与确认 / 名称查重 / 注册（带客户端 player_id UUID）/ 登录（含 file_hash 三态）/
重放去重 / 时间戳过期 / 未知动作 / 设备伪造 / 拉取式 peers & world（无 PUSH）/ 双客户端同屏 /
正常下线完整拆除 / 意外断开宽限期（期内重连不丢位置）/ 宽限期超时落库销毁（靠 peers 感知，无 leave 广播）。

`store_check.py` 覆盖：users 唯一冲突归类（设备 / user_id / user_name）、SAVEPOINT 回滚不污染事务、
登录查询与活跃时间、world + world_item 落库与 **rev 门槛三态**（落后被拒 / 相等被拒 / 领先写入）、
player 三围 + rev 门槛 + JOIN 带回账号名、`mark_all_offline`。

> v1.3 服务端**不再主动发任何帧**，一条请求对应（批量时对应多行）**按序**返回，无需再跳过 PUSH。
> 批量请求要读满 N 行响应：参考 `scripts/tcp_smoke_test.py::call_batch` / `read_frames`。

---

## 5. 配置项（全部在 `.env`）

| 键 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL 连接串 |
| `DB_BACKEND` | `postgres` | `postgres` / `memory` |
| `DB_POOL_MIN` / `DB_POOL_MAX` | 1 / 8 | 连接池上下限 |
| `HOST` / `PORT` | — / 5423 | **对外公布**的地址（日志与调试直连用；需与 server 组件的下发值一致） |
| `TCP_BIND` | `0.0.0.0` | TCP 实际监听地址 |
| `TCP_PORT` | 跟随 `PORT` | TCP 实际监听端口 |
| `DEVICE_ID_SECRET` | — | 设备 ID 的 HMAC 密钥（≥ 32 字节，**必填**） |
| `DEVICE_ID_MAX_AGE` | 0 | 设备 ID 有效期（秒），0 = 永不过期 |
| `REQUEST_TIME_WINDOW` | 300 | 报文时间戳容差（秒） |
| `DEBUG` | 0 | 1 = 调试日志 |
| `MAX_LINE_BYTES` | 16384 | 单行上限（device_id hex 后 2048 字符，勿小于 4096） |
| `READ_TIMEOUT` | 180 | 连接读超时（秒） |
| `GRACE_SECONDS` | 5 | 已登录连接意外断开后的宽限期（秒）：期内重连接管旧会话，超时则落库并销毁 |
| `MAX_CONNECTIONS` | 256 | 最大并发连接数 |
| `AUTOSAVE_INTERVAL` | 60 | 内存落库间隔（秒） |
| `USER_NAME_MAX_LEN` | 24 | 用户名称最大长度 |

---

## 6. 启动流程（s1 → s2 → s3 → s4）

`main.py::Service` 把启动拆成四个阶段，每阶段失败都能定位到具体步骤；
关闭时**逆序**执行注册的清理回调（LIFO），单条失败不影响其它回调。

| 阶段 | 标题 | 做什么 |
|---|---|---|
| s1 | 初始化 | ① 配置回写环境变量 ② 准备 `DEVICE_ID_SECRET`、建存储（连不上直接退出）、`ping()` 探活、会话表与账号业务 ③ 建 socket（**只建不绑**） ④ 动作表就绪，打印关闭回调栈 |
| s2 | 预热 | ① `mark_all_offline()` ② `game.load_from_store()` 全量载入热缓存 ③ 起自动落库定时器、起防外挂 ④ `bind()` 绑端口（**尚未 accept**） |
| s3 | 启动 | ① `tcp-accept` 线程开始收连接（v1.3：无推送线程） |
| s4 | 关闭 | ① 停定时器与防外挂 ② **最后落库** ③ 逆序执行关闭回调（hub → server → store） |

这样安排的原因：**s1 不碰端口也不碰连接池以外的资源**，`main.py check` 可以直接跑 s1 然后退出，
不会占用 5423；而「加载完数据才 bind」保证客户端一连上就能看到完整世界。

日志会逐条打印 `[s1] 初始化`、`[s2] 预热` … 便于线上定位卡在哪一步。

---

## 7. 数据模型（数据库 v2）

只有四张表，**库是唯一权威**。

| 表 | 主键 | 说明 |
|---|---|---|
| `users` | `device_id` | 设备 + 账号。`user_id` / `user_name` 未注册时为 `NULL`（PG 的 UNIQUE 允许多个 NULL） |
| `player` | `user_id` | 玩家数据，**一条玩家一行**：`player_id`（客户端 UUID）/ `file_hash`（登录文件校验）/ 名字 / 种族 / 坐标 / 朝向 / 在线 / 三围（`hp` / `stamina` / `mana` 及各自 `_max`） |
| `world` | `id` | 世界元信息：名称 / 随机种子，默认一行 `default` = 众生之门 |
| `world_item` | `id` | 世界实体（门 / 箱子 / 杆…），`world_id` 外键 |

- `admin` 表**暂不建**，只在 `schema.sql` 末尾留了设计注释（`admin_id` / `pass_hash` / `role` + 审计表）。
- 表名用 `users` 而不是 `user`（`user` 是 PG 保留字）。
- 每张业务表都带 `rev BIGINT`：**行级修订号**，既是增量落库的依据，也是冲突仲裁的依据。

### 7.1 只有「领先数据」能进库

所有 upsert 都带 `<表>.rev < EXCLUDED.rev` 门槛：

```sql
ON CONFLICT (user_id) DO UPDATE SET … , rev = EXCLUDED.rev
WHERE player.rev < EXCLUDED.rev
```

| 内存 rev vs 库 rev | 结果 |
|---|---|
| 更大 | 写入（`rowcount = 1`），库被推进 |
| 相等 | **不写**（`rowcount = 0`，计入 `stale`） |
| 更小 | 不写，计入 `stale`，记 WARNING「以库为准」 |

`game.py` 里还有一层「已落库修订号」（`_persisted_world_rev` / `_persisted_item_revs` / `_persisted_player_revs`）：
只有 `rev` 比它大的行才提交，所以挂机玩家一行也不写、高频上报的玩家每轮只写自己那一行，
库在远端也不会被打爆。

---

## 8. 设计要点

### 8.1 设备 ID：服务端签发，无状态校验

设备 ID 是一张 **1024 字节的「签名票据」**，结构：

```
[ver(2)][lenFlag(2)][ts(4)][nonce(32)][fp(16)][tag(32)][HKDF 扩展(N)]
```

- `tag = HMAC-SHA256(DEVICE_ID_SECRET, ver|lenFlag|ts | nonce | fp)`
- 最后一大段扩展由 `tag` 派生，因此**改任意一个字节都校验不过**
- 校验只看自身 HMAC，**不查库、不建会话**
- 客户端**不持密钥**（自己签等于把密钥发给全世界，且泄露后无法回收）

一个设备只能绑一个账号，一个账号永久绑首次登记的设备（不做设备迁移）。

### 8.2 热缓存 + 增量落库（数据库才是权威）

内存里的 `world` / `players` 只是**热缓存**，不是权威源：

- `state` 动作只改内存（不做 DB IO，高频上报也不怕），每行 `rev += 1`
- 每 `AUTOSAVE_INTERVAL` 秒只把**脏行**（`rev` 超过已落库值的行）upsert 一次；退出前在 s4 再存一次
- 库里 rev 更大 ⇒ 该行被拒，日志 WARNING「以库为准」，**不重试**（等下次上报带来更大的 rev）
- 落库失败不丢数据，写 `tmp/databaseErrordb.json` 兜底
- 重启加载时 `online` 一律强制 `False`（s2 之前已经 `mark_all_offline()`）

### 8.3 长连接 + 会话 Token（v1.3：拉取式，无推送）

- 一个 TCP 连接 = 一个 `Session`，刚连上是匿名的，`login`/`register` 后绑定账号
- **建连即内部签发 `session_token`**（`secrets.token_hex(16)`），但**不主动推送**，
  随首个 `hello` 响应下发；后续请求第 3 行带上它，不一致就只回 `SESSION_TOKEN_EXPIRED`（不再附推 reset_token）
- token 命中时不再重复查库确认绑定（只走形式校验），省下每请求 2KB 的 device_id
- **v1.3 取消了 `SessionHub` 的广播线程**：世界/在线数据由客户端拉 `peers` / `world`，频率自定（可批量）
- 同账号多会话时，最后一个断开才算下线（`has_user` 判定）

### 8.4 断线宽限期（区分“正常下线”与“意外断开”）

| 场景 | 路径 | 结果 |
|---|---|---|
| 客户端主动 `logout` | `actions.handle_logout` | 置离线 → 落库 → 销毁内存行 → 作废 token（v1.3：不广播 leave） |
| 连接意外断开（含读超时） | `server._teardown` → `hub.suspend` | 挂起 `GRACE_SECONDS`，**内存行不动**（仍算在线） |
| 期内同设备重连并 `login` | `hub.resume` | 摘掉挂起项、作废旧 token，位置/状态直接接着用 |
| 超时 | `hub._grace_loop` → `main._on_grace_expired` | `mark_offline` → `save_player` → `destroy_player`（v1.3：不广播 leave） |
| 超时后又重连 | `game.enter_world` | 内存无行时从库 `load_player` 重载（不是出生点），rev 继承库值所以能接着写 |

> `destroy_player` 会抬高 `_players_rev` 种子，否则销毁后重建的行从 0 开始会被 rev 门禁拒写（真丢数据）。

### 8.5 防重放（有局限）

`ReplayGuard` 按「会话 + 时间戳 + 正文 sha256」去重，只拦 `register` / `device` / `item` 这类写动作。
**跨连接重放挡不住**——协议目前没有 nonce 字段。详见 `protocol.md` §9。

---

## 9. 部署

systemd 示例在 `deploy/`：

- `the-door-of-bings.service.example` —— systemd，`ExecStart=uv run main.py serve`

本组件只监听 TCP，不走 nginx：nginx 的 stream 模块会把客户端 IP 换成 127.0.0.1，
服务端就拿不到真实来源了。直接在安全组 / ufw 里放行，同时记得把 `HOST` / `PORT`
配成公网地址，与 server 组件（HTTP 发现）的下发值保持一致：

```bash
ufw allow 5423/tcp
```

---

## 10. 与旧版的差异（1.x → 2.0）

旧版是 `app/` 下的 Flask + WebSocket 结构，2.0 彻底换掉：

| 方面 | 旧（≤ 1.x） | 新（2.0） |
|---|---|---|
| 游戏通道 | WebSocket `/ws/world` | **原生 TCP 4 行协议**，长连接 |
| 权限模型 | Cookie / 会话 | 协议头自带 device_id + 会话绑定 |
| `user_id` | 客户端生成 | **服务端生成**（`ZWME-2WKQ`） |
| device_id | 客户端自签（持密钥） | **服务端签发**，客户端只存不用算 |
| 发现接口 | `/game/api`（import 期请求 ipify） | `/api/v1/endpoint`（host/port 只读 `.env`） |
| 启动条件 | `HOST` == 公网 IP 才启动 | **永远启动**，`HOST` 只用于下发 |
| 帧读取 | `recv(65536)` 一次 | 带缓冲的行读取器（切包/粘包/2048 字符全 OK） |
| 事务 | 就地捕 UniqueViolation（事务已 aborted） | `SAVEPOINT` + `ROLLBACK TO SAVEPOINT` |
| 权威源 | 内存（定时覆盖库） | **数据库**，只有领先数据能进库 |
| 启动 | 一把梭 `main()` | 四阶段 s1/s2/s3/s4，关闭回调 LIFO |
| 错误响应 | 裸文本 | 统一 `FAIL \| action \| code \| message` |
| 测试 | 无 | `tcp_smoke_test.py` + `store_check.py` |

旧 `database.py` 兼容层已删除（`users.user` 签名已改成依赖 `Store`，直接 `import users` 即可）。

### 10.1 3.0 的变化（协议 v1.1 → v1.2，业务流程重构）

| 方面 | v1.1（2.0） | v1.2（3.0） |
|---|---|---|
| 会话身份 | 每请求带 device_id（2KB）查库确认 | **建连即推 `PUSH \| token`**，第 3 行扩成 3 字段，token 命中不查库 |
| 报文第 3 行 | `<device_id> \| <user_id>` | `<session_token> \| <device_id> \| <user_id>`（2 段仍接受） |
| 设备登记 | `device` 签发即 `touch_device` 入库 | 签发**不落库**，到 `register` 才写 `users` |
| 注册 | 直接 `register` | 新增 `check_name` 查重；payload 加第三段客户端 UUID `player_id` |
| 登录 | `login` 只认 user_id | JSON payload 带 `file_hash`，不一致拒登（`FILE_HASH_MISMATCH`） |
| 下线 | 关连接就算离线，只改 `online` | `logout` 七步完整拆除；意外断开走**宽限期** |
| 热缓存 | 玩家行常驻 | 销毁后重连从库 `load_player` 重载，位置不丢 |
| 错误码 | — | 新增 `BAD_SESSION_TOKEN` / `SESSION_TOKEN_EXPIRED` / `FILE_HASH_MISMATCH` |
| 冒烟测试 | 75 项 | **110 项**（含宽限期与重连接管用例） |

> 客户端（`game/code/net/`）本轮**不动**：v1.2 服务端仍接受 v1.1 报文，接上 token 后才拿到新能力。

---

## 11. 与 server 组件（HTTP 发现）的分工

| 职责 | 组件 |
|---|---|
| TCP 游戏协议、会话、拉取式 world/peers | **本组件（server-socket）** |
| 账号 / 设备 ID / 世界数据 / PostgreSQL | **本组件（server-socket）** |
| HTTP 发现接口 / 健康检查 | server（仓库 `server` 分支） |

两边配置各自独立（同名键 `HOST` / `PORT` 含义一致：对外公布的 TCP 地址），
改了要两边同步，否则客户端会被指到错误的地址。

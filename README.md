# 众生之门 服务端（The Door of All Beings · Server）

TCP 游戏协议 + HTTP 发现接口。无状态设备凭证、内存权威世界、定时落库。

| 项 | 值 |
|---|---|
| 协议版本 | **1.1**（4 行竖线报文，长连接，10Hz 推送） |
| 服务版本 | **2.0.0** |
| TCP 游戏端口 | `PORT`（默认 **5423**） |
| HTTP 发现端口 | `HTTP_PORT`（默认 **4231**） |
| 存储 | PostgreSQL（生产）/ 内存（调试与测试） |
| 依赖 | Python 3.12 · flask · waitress · psycopg2-binary · python-dotenv |

协议细节全部在 **[`protocol.md`](protocol.md)**（客户端对接看那一份就够）。

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
uv run main.py check           # 自检：配置 / 存储 / 设备密钥
uv run main.py                 # 同时起 TCP 5423 与 HTTP 4231
```

> ⚠️ `DEVICE_ID_SECRET` **定下来就不要改**。设备 ID 由它签名，
> 换了密钥所有老客户端存的 device_id 全部失效，老账号再也绑不上。

---

## 2. 命令行

```bash
uv run main.py serve     # TCP + HTTP（默认）
uv run main.py tcp       # 只起 TCP 游戏协议
uv run main.py http      # 只起 HTTP 发现 / 健康检查
uv run main.py check     # 自检：配置 / 存储 / 设备密钥 / 动作表
uv run main.py version   # 打印服务与协议版本
```

常用参数：`--debug`（调试日志 + HTTP 走 Werkzeug）、`--backend memory|postgres`。

---

## 3. 代码结构

```
server/
├─ main.py          入口：组装 Service、日志、CLI
├─ config.py        所有配置集中在这里（只从 .env / 环境变量读）
├─ protocol.py      协议 v1.1：解析 / 编码 / 错误码
├─ server.py        TCP 监听、帧读取、单连接线程、防重放
├─ session.py       长连接会话（Session）与会话表 + 10Hz 推送线程（SessionHub）
├─ actions.py       动作注册表（@action），业务入口
├─ users.py         账号业务：设备登记 / 注册 / 登录
├─ device_id.py     设备 ID 的签发与无状态校验（HMAC）
├─ game.py          内存权威：世界物品 + 在线玩家，定时落库
├─ store.py         存储抽象：PostgresStore / MemoryStore
├─ db.py            PostgreSQL 连接池
├─ http_api.py      HTTP 发现接口 + /healthz
├─ schema.sql       表结构（幂等，可重复执行）
├─ protocol.md      协议规范（客户端对接看这份）
├─ deploy/          nginx 与 systemd 示例
└─ scripts/
   ├─ init_db.py        建表 / 升级
   ├─ tcp_smoke_test.py 协议冒烟测试（无需数据库）
   └─ store_check.py    PostgreSQL 存储层校验（跑完自清理）
```

---

## 4. 校验与测试

服务端没有引入 pytest：两个脚本都是零额外依赖，退出码 0 = 全绿。

```bash
# 协议全链路（内存后端，起真 TCP，不用数据库）
uv run scripts/tcp_smoke_test.py          # 当前 75/75
uv run scripts/tcp_smoke_test.py -v       # 逐条打印

# PostgreSQL 存储层（用真库，但只碰自己造的临时数据，跑完清理）
uv run scripts/store_check.py             # 当前 12/12

# 配自检（真实生产环境跑这条最合适）
uv run main.py check
```

`tcp_smoke_test.py` 覆盖：握手 / 设备签发与确认 / 注册 / 登录 / 重放去重 /
哈希篡改 / 时间戳过期 / 未知动作 / 设备伪造 / 10Hz PUSH / 双客户端同屏 / 断线下线。

---

## 5. 配置项（全部在 `.env`）

| 键 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL 连接串 |
| `DB_BACKEND` | `postgres` | `postgres` / `memory` |
| `DB_POOL_MIN` / `DB_POOL_MAX` | 1 / 8 | 连接池上下限 |
| `HOST` / `PORT` | — / 5423 | **对外公布**的地址与 TCP 端口（只给发现接口用） |
| `TCP_BIND` | `0.0.0.0` | TCP 实际监听地址 |
| `HTTP_BIND` / `HTTP_PORT` | `0.0.0.0` / 4231 | HTTP 监听地址与端口 |
| `DEVICE_ID_SECRET` | — | 设备 ID 的 HMAC 密钥（≥ 32 字节，**必填**） |
| `DEVICE_ID_MAX_AGE` | 0 | 设备 ID 有效期（秒），0 = 永不过期 |
| `REQUEST_TIME_WINDOW` | 300 | 报文时间戳容差（秒） |
| `DEBUG` | 0 | 1 = 调试日志 + HTTP 走 Werkzeug |
| `MAX_LINE_BYTES` | 16384 | 单行上限（device_id hex 后 2048 字符，勿小于 4096） |
| `READ_TIMEOUT` | 180 | 连接读超时（秒） |
| `MAX_CONNECTIONS` | 256 | 最大并发连接数 |
| `PUSH_HZ` | 10 | 世界快照推送频率 |
| `AUTOSAVE_INTERVAL` | 60 | 内存落库间隔（秒） |
| `USER_NAME_MAX_LEN` | 24 | 用户名称最大长度 |

---

## 6. 设计要点

### 6.1 设备 ID：服务端签发，无状态校验

设备 ID 是一张 **1024 字节的「签名票据」**，结构：

```
[ver(2)][lenFlag(2)][ts(4)][nonce(32)][fp(16)][tag(32)][HKDF 扩展(N)]
```

- `tag = HMAC-SHA256(DEVICE_ID_SECRET, ver|lenFlag|ts | nonce | fp)`
- 最后一大段扩展由 `tag` 派生，因此**改任意一个字节都校验不过**
- 校验只看自身 HMAC，**不查库、不建会话**
- 客户端**不持密钥**（自己签等于把密钥发给全世界，且泄露后无法回收）

一个设备只能绑一个账号，一个账号永久绑首次登记的设备（不做设备迁移）。

### 6.2 内存权威 + 定时落库

世界物品与在线玩家以内存为准，数据库只是快照：

- `state` 动作只改内存（不做 DB IO，10Hz 上报也不怕）
- 每 `AUTOSAVE_INTERVAL` 秒 upsert 一次；退出前再存一次
- 落库失败不丢数据，写 `tmp/databaseErrordb.json` 兼底
- 每个集合带修订号 `rev`：推送时只带变化的部分

### 6.3 长连接 + 10Hz 推送

- 一个 TCP 连接 = 一个 `Session`，刚连上是匿名的，`login`/`register` 后绑定账号
- `SessionHub` 单线程按 `PUSH_HZ` 广播，世界快照每 tick 只构造一次
- 同账号多会话时，最后一个断开才算下线（`has_user` 判定）

### 6.4 防重放（有局限）

`ReplayGuard` 按「会话 + 时间戳 + 正文 sha256」去重，只拦 `register` / `device` / `item` 这类写动作。
**跨连接重放挡不住**——协议目前没有 nonce 字段。详见 `protocol.md` §9。

---

## 7. 部署

两个示例都在 `deploy/`：

- `nginx-the-door-of-bings.conf.example` —— 只反代 HTTP 发现接口（4231）
- `the-door-of-bings.service.example` —— systemd，`ExecStart=uv run main.py serve`

**TCP 5423 不经 nginx**：nginx 的 stream 模块会把客户端 IP 换成 127.0.0.1，
服务端就拿不到真实来源了。直接在安全组 / ufw 里放行：

```bash
ufw allow 5423/tcp
```

---

## 8. 与旧版的差异（1.x → 2.0）

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
| 错误响应 | 裸文本 | 统一 `FAIL \| action \| code \| message` |
| 测试 | 无 | `tcp_smoke_test.py` + `store_check.py` |

旧 `database.py` 兼容层已删除（`users.user` 签名已改成依赖 `Store`，直接 `import users` 即可）。

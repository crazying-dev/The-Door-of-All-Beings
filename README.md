# 众生之门 · 账号服务端（The Door of All Beings Server）

《众生之门》（罗小黑战记同人二创）客户端的账号 / 设备绑定服务，对应 Godot 项目仓库
[`The-Door-of-All-Beings`](https://github.com/crazying-dev/The-Door-of-All-Beings) 的 `Server` 分支。

> 本项目为粉丝二创作品，与《罗小黑战记》官方无关；IP 版权归寒木春华动画工作室所有。

---

## 产品规则（服务端强制执行）

| 规则 | 实现方式 |
|---|---|
| 账号只能存在于首次激活的设备上 | `accounts.device_id` 为 `UNIQUE`；`/api/v1/` 每次都重新比对 |
| 同一设备不能注册多个账号 | `accounts.device_id` 唯一约束 → `DEVICE_ALREADY_BOUND` |
| 设备标识由服务端签发并落库 | `POST /api/v1/device` 生成 UUID 写入 `devices` 表 |
| 用户 ID 由服务端生成 | `/api/v1/register` 生成 `XXXX-XXXX` |
| 用户名称全局唯一 | `accounts.user_name` 为 `UNIQUE` |
| 为「角色死亡后档案注销 / 30 分钟禁登」预留 | `accounts.status` + `accounts.locked_until` |

---

## 目录结构

```
The-Door-Of-Bings-Server/
├── main.py                  # 开发环境入口（Flask 内置服务器）
├── wsgi.py                  # 生产环境 WSGI 入口（waitress）
├── schema.sql               # 表结构（幂等，可重复执行）
├── pyproject.toml
├── uv.lock                  # 依赖锁定文件（入库，保证各机器安装一致）
├── .python-version          # 固定 Python 3.12（uv 缺则自动下载）
├── .env.example             # 配置模板（.env 不入库）
├── app/
│   ├── __init__.py          # Flask 应用工厂 + 统一错误处理
│   ├── config.py            # 读取 .env
│   ├── db.py                # psycopg2 连接池 / 建表 / 探活
│   ├── errors.py            # 错误码表 + ApiError
│   └── api/v1.py            # 4 个业务接口
├── scripts/
│   ├── init_db.py           # 建表
│   └── smoke_test.py        # 端到端冒烟测试（会自动清理临时记录）
└── deploy/                  # nginx / systemd 配置模板
```

技术栈：**Python 3.12 + Flask 3 + PostgreSQL**（`psycopg2-binary`，不使用 ORM）。

---

## 快速开始

前置条件只有一条：**装好 [uv](https://docs.astral.sh/uv/)**。

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows（PowerShell）
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

之后**不需要 `uv venv`、不需要 `pip install`、不需要手动激活虚拟环境**，一条 `uv run` 就把环境和依赖准备好并直接执行脚本：

```bash
# ① 配置连接串（.env 已在 .gitignore 中，不会入库）
cp .env.example .env          # Windows: copy .env.example .env
#   编辑 .env，填入 DATABASE_URL

# ② 建表（幂等）—— 首次运行会自动创建 .venv 并安装依赖
uv run scripts/init_db.py

# ③ 开发运行（Flask 内置服务器）
uv run main.py

# ④ 生产运行（waitress）
uv run waitress-serve --host 127.0.0.1 --port 2690 wsgi:app

# ⑤ 端到端自测（需 ③ 或 ④ 已启动）
uv run scripts/smoke_test.py
```

### 一条 `uv run` 背后发生了什么

| 步骤 | uv 自动完成 |
|---|---|
| 1 | 读 `.python-version` → 本机没有 Python 3.12 时**自动下载**一份到 uv 的托管目录 |
| 2 | 在项目根创建 `.venv`（可用环境变量 `UV_PROJECT_ENVIRONMENT` 改路径） |
| 3 | 按 `uv.lock` 精确安装 flask / psycopg2-binary / python-dotenv / waitress，并自动同步 |
| 4 | 用该环境执行你传入的脚本或命令 |

所以一个刚克隆下来的仓库是**零准备可运行**的。

### 常用命令对照

| 目的 | 命令 |
|---|---|
| 建/更新环境与依赖（隐式） | `uv run main.py` |
| 显式同步环境 | `uv sync` |
| 按锁文件精确安装（CI / 部署） | `uv sync --frozen` |
| 校验锁文件是否与 `pyproject.toml` 一致 | `uv lock --check` |
| 临时跑一段 Python | `uv run python -c "import flask; print(flask.__version__)"` |
| 不改锁文件地运行（部署/离线） | `uv run --frozen --no-sync main.py` |

> 为什么没有 `uv run serve` 这类命令别名？本项目是扁平结构的**应用**而非库（`package = false`），
> uv 的入口点表 `[project.scripts]` 只在项目被构建安装后才生效，所以约定直接用脚本路径运行。

---

## API 契约 v1

BASE：`https://the-door-of-bings.yjlt.top`
所有请求与响应均为 `application/json; charset=utf-8`（响应中的中文不会被转义）。
成功响应一定含 `"ok": true`；失败响应一定含 `"ok": false` + `code` + `msg`。

### 1. `GET /api/v1/health`

```json
{ "ok": true, "service": "the-door-of-bings", "version": "1.0.0", "database": "up", "time": "2026-10-02T05:41:53+00:00" }
```

### 2. `POST /api/v1/device` — 签发设备标识

请求体可为 `{}`（总是签发新 UUID）：

```json
{ "ok": true, "device_id": "ca266db4-b919-42dc-b7cc-a6aa32c0dbfa", "issued": true, "account": null }
```

请求体带 `{"device_id": "..."}` 时为**幂等**语义：已知则原样返回并刷新 `last_seen_at`（`issued: false`），
未知则收编入库（用于兼容旧版本客户端本地已存在的设备标识）。

无论哪种情形，响应都会带一个 `account` 字段：该设备**已绑定**的账号，未绑定则为 `null`：

```json
{ "ok": true, "device_id": "ca266db4-...", "issued": false,
  "account": { "user_id": "ZWME-2WKQ", "user_name": "某位旅人", "status": "active" } }
```

客户端据此可以在本地 `load.bin` 丢失时恢复登录（设备即身份，无需重新注册）。

### 3. `POST /api/v1/register` — 注册

```json
// 请求
{ "device_id": "ca266db4-...", "user_name": "某位旅人" }

// 成功
{ "ok": true, "user_id": "ZWME-2WKQ", "user_name": "某位旅人", "device_id": "ca266db4-..." }

// 失败
{ "ok": false, "code": "DEVICE_ALREADY_BOUND", "msg": "该设备已绑定账号，不能重复注册" }
```

### 4. `POST /api/v1/verify` — 登录校验（设备锁执行点）

```json
// 请求（三者必须同时提供）
{ "device_id": "ca266db4-...", "user_id": "ZWME-2WKQ", "user_name": "某位旅人" }

// 成功
{ "ok": true, "user_id": "ZWME-2WKQ", "user_name": "某位旅人", "device_id": "ca266db4-..." }
```

### 错误码

| code | HTTP | 说明 |
|---|---|---|
| `BAD_REQUEST` | 400 | 请求体不是 JSON 对象、缺字段、字段类型/长度非法 |
| `DEVICE_UNKNOWN` | 404 | 设备未登记（需先调用 `/api/v1/device`） |
| `USER_NOT_FOUND` | 404 | `user_id` 不存在（旧账号/换设备/数据被删） |
| `DEVICE_MISMATCH` | 403 | 账号绑定的是别的设备 —— **设备锁** |
| `NAME_MISMATCH` | 403 | 用户名称与该账号不符 |
| `DEVICE_ALREADY_BOUND` | 409 | 该设备已绑定账号，不能重复注册 |
| `NAME_TAKEN` | 409 | 该名称已被占用 |
| `ACCOUNT_LOCKED` | 403 | 账号或设备被停用 / 处于禁登期 |
| `NOT_FOUND` | 404 | 接口路径不存在 |
| `INTERNAL` | 500 | 服务器内部错误 |

---

## 数据库表结构

```sql
CREATE TABLE devices (
    device_id    TEXT PRIMARY KEY,          -- 服务端签发的 UUID
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    user_id       TEXT PRIMARY KEY,         -- 形如 ZWME-2WKQ
    device_id     TEXT NOT NULL UNIQUE REFERENCES devices (device_id) ON DELETE CASCADE,
    user_name     TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL DEFAULT 'active',
    locked_until  TIMESTAMPTZ,              -- 预留：角色死亡后 30 分钟禁登
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
```

---

## 部署

`deploy/` 下提供了模板，把占位符换成实际值即可。

1. 拉取代码到服务器（例如 `/root/The-Door-Of-Bings-Server`，即 `Server` 分支的 worktree 所在目录）：

   ```bash
   # 若服务器还没有 uv
   curl -LsSf https://astral.sh/uv/install.sh | sh

   git clone -b Server https://github.com/crazying-dev/The-Door-of-All-Beings.git /root/The-Door-Of-Bings-Server
   cd /root/The-Door-of-Bings-Server

   uv sync --frozen                    # 按 uv.lock 精确安装（不改锁文件）
   cp .env.example .env && vi .env      # 填 DATABASE_URL
   uv run --frozen scripts/init_db.py   # 建表（幂等）
   ```

2. 用 systemd 守护（或用 tmux，与 forum 的部署习惯保持一致）：

   ```bash
   cp deploy/the-door-of-bings.service.example /etc/systemd/system/the-door-of-bings.service
   systemctl daemon-reload && systemctl enable --now the-door-of-bings
   systemctl status the-door-of-bings
   ```

3. nginx 反代 + HTTPS 证书：

   ```bash
   cp deploy/nginx-the-door-of-bings.conf.example /etc/nginx/conf.d/the-door-of-bings.conf
   certbot --nginx -d the-door-of-bings.yjlt.top
   nginx -t && systemctl reload nginx
   ```

4. 验证：`curl https://the-door-of-bings.yjlt.top/api/v1/health`

> 应用只监听 `127.0.0.1:2690`，不直接对外暴露；HTTPS 与证书由 nginx 负责。

---

## 客户端对接（Godot）

客户端（`code/StartCreen/`）的登录流程已改为：

1. 首次运行：`POST /api/v1/device`（body `{}`）拿到 `device_id`，写入 `user://InstallationUniqueID.bin`；
2. 输入名称 → `POST /api/v1/register` → 拿到 `user_id`，写入 `user://load.bin`；
3. 之后每次启动：读取两个文件 → `POST /api/v1/verify` → 通过则进入游戏；
4. 校验失败时：`USER_NOT_FOUND` / `DEVICE_UNKNOWN` → 重新走 1、2；
   `DEVICE_MISMATCH` / `NAME_MISMATCH` / `ACCOUNT_LOCKED` → 提示用户，不再放行。

全部请求走 **HTTPS**，并设置了超时与重试上限（旧版本用的是 `http://yjlt.top/TheDoorOfBings/UUID4/`，已废弃）。

---

## 安全说明

* `DATABASE_URL` 只存在于服务器上的 `.env`（已被 `.gitignore` 忽略），**任何情况下都不要提交**；
* `device_id` 只是设备标识，不是认证凭据；真正的放行依据是服务端比对的「设备 + 用户 ID + 名称」三元组；
* 建议为应用单独建一个数据库账号，只授予 `thedoorofbings` 库的 CRUD 权限，不要使用 `postgres` 超级用户。

---

## 许可

BSD 3-Clause，与主仓库一致。

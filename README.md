# 众生之门 HTTP 发现服务（The Door of All Beings · HTTP Discovery）

本组件是 **纯 Flask HTTP 服务**：告诉客户端该连哪里 + 健康检查。

游戏本体的 TCP 长连接协议（4 行竖线报文、10Hz 推送、账号 / 设备 / 世界数据）
已拆分为独立组件 **`server-socket`**（仓库同名分支），两边互不依赖、可分别部署与重启。

| 项 | 值 |
|---|---|
| 协议版本 | **1.1**（仅随发现接口下发，供客户端做兼容提示） |
| 服务版本 | **2.0.0** |
| HTTP 监听 | `HTTP_BIND:HTTP_PORT`（默认 `127.0.0.1:4231`） |
| 依赖 | Python 3.12 · flask · waitress · python-dotenv |

TCP 协议细节在 **server-socket 组件的 `protocol.md`**（客户端对接看那一份就够）。

---

## 1. 快速开始

```bash
cp .env.example .env   # Windows: copy .env.example .env
uv run main.py         # 起 HTTP 发现服务（默认 4231）
```

`.env` 里唯一要注意的是 `HOST` / `PORT`：这是**下发给客户端的 TCP 游戏服地址**
（server-socket 组件对外监听的公网地址），本组件自己不连接它。

---

## 2. 命令行

```bash
uv run main.py         # 启动 HTTP 发现服务（默认 serve）
uv run main.py serve   # 同上
uv run main.py check   # 自检：配置 / HOST 是否已配置
uv run main.py version # 打印服务与协议版本
```

常用参数：`--debug`（调试日志 + HTTP 走 Werkzeug）。

---

## 3. 接口

| 路径 | 说明 |
|---|---|
| `GET /` | 服务信息与可用路径 |
| `GET/POST /api/v1/endpoint` | 发现接口：返回 TCP 游戏服 host/port、协议与服务版本 |
| `GET/POST /game/api` | 旧客户端发现路径，保留一个版本周期做过渡 |
| `GET /healthz` | 健康检查（uptime + 版本），可直接给负载均衡 / 监控用 |

---

## 4. 代码结构

```
server/
├─ main.py          入口：初始化 / 启动 / 关闭（阶段名沿用 s1/s3/s4）
├─ lifecycle.py     阶段日志与关闭回调栈（LIFO）
├─ config.py        所有配置集中在这里（只从 .env / 环境变量读）
├─ http_api.py      Flask 应用：发现接口 + /healthz（可后台起、可关闭）
└─ deploy/          nginx 与 systemd 示例
```

---

## 5. 配置项（全部在 `.env`）

| 键 | 默认 | 说明 |
|---|---|---|
| `HOST` | — | **对外公布**的 TCP 游戏服地址（只给发现接口下发用） |
| `PORT` | 5423 | **对外公布**的 TCP 端口（同上） |
| `HTTP_BIND` / `HTTP_PORT` | `0.0.0.0` / 4231 | HTTP 监听地址与端口 |
| `DEBUG` | 0 | 1 = 调试日志 + HTTP 走 Werkzeug |

---

## 6. 部署

两个示例都在 `deploy/`：

- `nginx-the-door-of-bings.conf.example` —— 反代本服务的 HTTP 发现接口（4231）
- `the-door-of-bings.service.example` —— systemd，`ExecStart=uv run main.py serve`

TCP 5423 **不经 nginx**（由 server-socket 组件监听），在安全组 / ufw 里直接放行即可。

---

## 7. 与 server-socket 组件的分工

| 职责 | 组件 |
|---|---|
| HTTP 发现接口 / 健康检查 | **本组件（server）** |
| TCP 游戏协议、会话、10Hz 推送 | server-socket |
| 账号 / 设备 ID / 世界数据 / PostgreSQL | server-socket |

两边配置各自独立（同名键 `HOST` / `PORT` 含义一致：对外公布的 TCP 地址）。
改了 `HOST` / `PORT` 时记得两边同步，否则客户端会被指到错误的地址。

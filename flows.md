# 众生之门 业务流程规范（server-socket）

> 本文档站在 **客户端视角** 描述完整业务流程，并标注对应的服务端动作。
> 底层报文格式见 [`protocol.md`](protocol.md)。
> **实现前请先对齐设计，代码按本文档重构。**

---

## 0. 核心概念：会话 Token

**每次 TCP 连接建立后，服务端在内部立即签发一个会话 Token（`secrets.token_hex(16)`，32 位 hex）**，
随**首个 `hello` 响应**下发给客户端（v1.3：不再建连主动推送），后续所有业务请求必须携带此 Token，
服务端据此识别是哪个会话。

```
客户端 connect TCP  →  hello  ←  OK | hello | {"…","session_token":"<hex32>","server_push":false,"push_hz":0}
```

| 属性 | 说明 |
|---|---|
| 作用域 | 单条 TCP 连接（断开即失效） |
| 用途 | 标识会话身份，替代当前「每请求都带 device_id + user_id」的方式 |
| 生命周期 | hello 响应下发 → 下线/超时/意外断开后销毁 |
| 重置 | 若玩家掉线后重连，重新走 hello 拿到新 Token（旧 Token 作废） |

> **规则**：除首条 `hello`（此时还没有 token，第 3 行按 2 段发）外，所有请求第 3 行都携带 `session_token`。

---

## 1. 注册（新玩家首次进入游戏）

### 时序

```
客户端                                     服务端
  │                                          │
  │  ── 本地准备 ──                           │
  │  1. 获取设备标识:                          │
  │     · 读取启动参数 --PID                   │
  │     · 若无则本地生成 UUID（设备指纹）       │
  │  2. 本地已有 device_id ?                  │
  │     · 有 → 跳到步骤 4                     │
  │     · 无 → 继续步骤 3                     │
  │                                          │
  │  ── 设备注册（仅新机） ──                   │
  │  3. 发送 device 请求 ────────────────────→│ 生成完整 device_id（HMAC 签名）
  │     ←────────────── OK | device | {...}  │ **不入库**，仅返回给客户端存储
  │     客户端本地持久化 device_id              │
  │                                          │
  │  ── 账号注册 ──                            │
  │  4. 玩家输入名称（UI 层）                   │
  │                                          │
  │  5. 发送 check_name 请求 ───────────────→│ 查询 users 表 user_name 是否已存在
  │     ←── OK {"available":true/false}      │
  │     若 false → 回到步骤 4 重新输入          │
  │                                          │
  │  6. 本地生成 UUID 作为 player_id           │
  │                                          │
  │  7. 发送 register 请求 ─────────────────→│ 验证 device_id 合法性
  │     携带: session_token                   │ 验证 device_id 未被绑定
  │           device_id                       │ 验证 user_name 不重复
  │           player_id (UUID)                │ 生成 user_id（服务端权威）
  │           user_name                       │ 写入 users + player 表
  │     ←── OK | register | {...}            │ 绑定 device_id → user_id
  │                                          │
  │  8. 注册完成，本地持久化 player_id/user_id   │
  │                                          │
```

### 关键点

- **device_id 不入库**：新机阶段只生成、不关联账号，等 register 时才一起落库
- **player_id**：由客户端生成 UUID（本地唯一标识），服务端收到后生成自己的 `user_id` 作为权威主键
- **名称查重**：独立步骤，便于客户端做即时反馈，不用等注册全流程走完才发现重名
- **启动参数 `--PID`**：优先使用外部传入的进程/设备标识，方便调试或多实例区分

---

## 2. 登录（已有账号的老玩家）

### 时序

```
客户端                                     服务端
  │                                          │
  │  ── 连接建立 ──                           │
  │  1. connect TCP ───────────────────────→│ accept（不发任何帧）
  │     → hello                               │ 内部签发会话 Token
  │     ←── OK | hello | {session_token,…}    │ 随响应下发 token
  │                                          │
  │  ── 登录请求 ──                           │
  │  2. 发送 login 请求 ───────────────────→│
  │     携带: session_token                   │ 校验 session_token 有效
  │           device_id                      │ 校验 device_id HMAC 签名
  │           player_id (user_id)            │ 校验 player_id 存在
  │           file_hash                      │ 校验 file_hash 与库中记录一致
  │                                          │
  │     ←── OK | login | {user_name, ...}    │ 标记 player.online = True
  │         或 FAIL | login | <code> | <msg> │ session 绑定身份
  │                                          │
  │  3. 进入世界，按自选频率上报 state + 拉取 peers/world │
  │                                          │
```

### 关键点

- **file_hash（登录校验文件哈希）**：客户端本地关键文件的哈希，服务端校验完整性；
  首次登录时由服务端初始化（register 成功后返回），后续登录时客户端必须回传一致值
- **session_token 随首个 `hello` 响应下发**（v1.3 不再建连主动推送），客户端存下后续都带上
- 一个 device_id 只能绑定一个账号（永久），不做设备迁移

---

## 3. 正常下线

### 时序

```
客户端                                     服务端
  │                                          │
  │  1. 发送 logout 请求 ──────────────────→│
  │     携带: session_token                   │ 校验 session_token
  │                                          │ player.online = False
  │                                          │ 数据落库（player + world_items 脏行）
  │                                          │ 销毁 player 内存缓存
  │                                          │ 销毁 session_token
  │     ←── OK | logout | {}                 │
  │                                          │
  │  2. 客户端主动 close TCP ───────────────→│ 清理 socket 资源
  │                                          │ 会话结束
```

### 服务端动作顺序（严格）

1. 将 player 内存状态 flush 到数据库（`game.save_one(player)`）
2. 从 `SessionHub` 移除该 session
3. 销毁 player 的热缓存行（后续同玩家重连时从库重新加载）
4. Token 作废（标记为已消费）
5. 返回 `OK | logout`（**不再广播 leave**：其他客户端拉 `peers` 时自然感知其消失）
6. 等待客户端断开 / 服务端主动关闭

---

## 4. 超时 / 意外断开（服务端视角）

```
服务端检测到连接异常（recv 返回 0 / 异常 / READ_TIMEOUT 触发）
  │
  ├─→ 【5 秒等待窗口】保持 player 热缓存不销毁，session 标记为 "suspend"
  │     │
  │     ├─ 5s 内客户端重连（见流程 5）→ 恢复会话，token 可能重置
  │     │
  │     └─ 5s 超时 →
  │           ① player 数据落库
  │           ② 销毁 player 内存缓存
  │           ③ 销毁 session_token（标记过期）
  │           ④ player.online = False
  │           （v1.3：不再向其他玩家广播 leave，他们下次拉 peers 自然看不到他）
  │
  └─→ 若超时后又收到该玩家的请求（携带旧 token）→
        服务端返回: FAIL | <action> | SESSION_TOKEN_EXPIRED | …（不再附推 reset_token）
        客户端收到后走【流程 5 重连】重新 hello 拿新 token → 重新登录 → player 从库重载
```

### 关键点

- **5 秒窗口**是给"网络闪断"一个自愈机会，不立即销毁数据，减少不必要的库读写
- 窗口期内 player 仍算在线（不出现在下线广播中），其他玩家看到的仍是他在场
- 超时后 token 被销毁：后续拿旧 token 来的请求会被拒绝并提示重置

---

## 5. 超时 / 意外断开（客户端视角）

```
客户端检测到断开（send/recv 异常 / 心跳无响应）
  │
  ├─→ [5 秒重连窗口]
  │     │
  │     ├─ 尝试 connect 同一 host:port
  │     │     │
  │     │     ├─ 成功 →
  │     │     │     重新发送 hello → 从响应拿新 session_token
  │     │     │     │
  │     │     │     ├─ 若后续请求收到 SESSION_TOKEN_EXPIRED →
  │     │     │     │     更新本地 token
  │     │     │     │     重新发送 login 请求（携带新 token）
  │     │     │     │     恢复游戏世界
  │     │     │     │
  │     │     │     └─ 正常 → 用新 token 发 login → 进入游戏
  │     │     │
  │     │     └─ 失败（网络仍不通）→
  │     │           提醒用户「连接丢失，正在重试…」
  │     │           等待用户点击重试 / 自动重试（指数退避）
  │     │           重来一遍流程 5
  │     │
  │     └─ 超过 5s 仍无法连接 →
  │           回退到完整流程：
  │           重新走流程 2（登录）从 connect 开始
```

### 关键点

- 客户端 **不需要也不应该** 在重连后继续使用旧 token——连接一断旧 token 即废
- 新 token 由服务端在新连接的 `hello` 响应里下发（与流程 2 步骤 1 相同）
- 服务端**不再发 `reset_token` 推送**：token 失效时只回一帧 `SESSION_TOKEN_EXPIRED`，客户端据此重新 hello + login
- 重连成功后 player 数据由服务端从库中重载（因为超时窗口可能已经销毁了缓存）

---

## 6. 完整会话生命周期（总览）

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                         TCP 连接建立                              │
 │         服务端内部签发 token，随首个 hello 响应下发             │
 └────────────────────────────┬────────────────────────────────────┘
                              │
                 ┌────────────▼────────────┐
                 │   新玩家？               │
                 │ 有 device_id + 账号 ?    │
                 └──┬─────────────────┬────┘
                    │ 否               │ 是
                    ▼                 ▼
          ┌──────────────┐    ┌──────────────┐
          │ 流程 1: 注册  │    │ 流程 2: 登录  │
          └──────┬───────┘    └──────┬───────┘
                 │                   │
                 └─────────┬─────────┘
                           ▼
                 ┌───────────────────┐
                 │   进入游戏世界     │
                 │ 拉取 peers/world + 上报  │
                 └────────┬──────────┘
                          │
            ┌─────────────┼──────────────┐
            ▼             ▼              ▼
     流程 3: 正常下线  流程 4/5: 断开  服务端关闭(s4)
```

---

## 7. v1.3 报文变更小结

相对 v1.2，本版（v1.3）在服务端与客户端共同实现以下变更：

| 位置 | 变更 | 说明 |
|---|---|---|
| 会话 Token 下发 | 取消建连 `PUSH token` | 改为随首个 `hello` 响应的 `session_token` 字段下发 |
| token 失效 | 取消 `PUSH reset_token` | 只回一帧 `FAIL SESSION_TOKEN_EXPIRED`，客户端自行重新 hello + login |
| 世界/在线数据 | 取消 10Hz `PUSH world` 与 `PUSH event` | 改为客户端拉取 `peers` / `world`，频率自定（可批量） |
| 请求尾哈希 | 仅正文哈希 → 头+正文联合哈希 | `sha256(第1行 + 第2行 + 第3行)`，改头部即失效 |
| 第 2 行正文 | 单 action → 可批量多 action | 多组用 `;;` 连接，服务端逐个执行、按序回多帧 |

> 过渡期：v1.2 及更早报文仍被接受（哈希按仅正文算、无批量）；服务端按第 1 行的 version 自动区分。
> `check_name` / `file_hash` / `session_token` 列等字段自 v1.2 引入以来保持不变。

---

## 8. 术语对照

| 术语 | 含义 |
|---|---|
| device_id | 设备票据（服务端 HMAC 签发，客户端持久化存储，一机一ID） |
| player_id | 客户端本地生成的 UUID，作为玩家本地标识；服务端收到后映射为自己的 user_id |
| user_id | 服务端权威的玩家主键（register 时服务端生成） |
| session_token | 本次 TCP 连接的会话令牌，服务端签发，所有后续请求必须携带 |
| file_hash | 客户端关键文件的哈希值，用于登录完整性校验 |
| --PID | 客户端启动参数，传入的设备/进程标识，优先于本地生成 |

# 客户端网络层（code/net）

服务端协议见 [server/protocol.md](../../../server/protocol.md)（4 行竖线报文 + 单行长响应 + 10Hz PUSH）。

| 文件 | 类型 | 职责 |
|---|---|---|
| `net_config.gd` | RefCounted | 服务端地址的唯一来源：环境变量覆盖 + 生产默认值 |
| `tcp_client.gd` | Node | 协议层：建连、组包、收帧、串行化请求、超时。**不懂业务** |
| `game_api.gd` | Node | 业务层：hello / device / register / login / state / item，并维护本机设备与当前账号 |
| `net_sync.gd` | Node | 世界场景的同步层：10Hz 上报 + 消费 PUSH world / event + 远端玩家代理 |

## 启动流程

```
StartCreen.tscn / User(userDOM.gd)
  → GameApi.connect_and_hello()      握手，拿服务端版本 / push_hz / user_name_max_len
  → GameApi.ensure_device()          本地有 hex 就“确认”，没有就申一张（服务端签发）
  → 本地有账号 ? login(user_id) : register(user_name)
  → 进入下一场景（race.tscn → world.tscn）
        → net_sync.setup(player, world)
        → GameApi.login_with_saved_account()   重新建连并静默登录
        → _process 里每 0.1s 上报 state，同时收 PUSH world
```

登录成功后同一连接上的后续请求**不再携带 device / user_id**（身份已记在会话上），
这样每帧省掉 2KB 的 hex。

## 本地联调

```bat
:: 客户端（窗口内执行，或直接写进启动器的环境）
set TDOB_SERVER_HOST=127.0.0.1
set TDOB_SERVER_PORT=5423
:: 服务端（server/ 目录，用内存后端，不碰 PostgreSQL）
set DB_BACKEND=memory
uv run main.py serve
```

## 几个必须知道的细节

- **请求是串行的**：服务端回帧不带请求 id，并发就会错配；`tcp_client.request()` 用
  `_busy` + `await process_frame` 当互斥锁，后来的请求排队。
- **PUSH 不进 `_inbox`**：`PUSH | world | {...}` 直接走 `pushed` 信号，不会干扰正在等的响应；
  等待时只接受 `action` 匹配（或 `-`）的帧，超时丢弃的老响应会被忽略。
- **body hash 逐字节算**：`sha256(第2行原文 + "\n" + 第3行原文)`，不要 strip，
  否则会得到 `BAD_PLAIN_HASH`。
- **FAIL 帧装不下 `account`**：`DEVICE_ALREADY_BOUND` 时要再调一次 `device`（行③带本机 device）
  才能拿回绑定账号，见 `userDOM._adopt_bound_account()`。
- **`BAD_DEVICE_ID` 自愈**：本地存的 hex 校验不过（换密钥 / 旧版遗留）时，
  `GameApi.ensure_device()` 会自动丢掉本地文件重领一张。
- **远端代理**：`net_sync` 实例化 `Scenes/actors/player.tscn`，把 `preview_keys` 关掉
  （不然按 1/2/3 也会切它们的动画），位置是插值过去的，动作按 `state` 字段切 walk / idle。

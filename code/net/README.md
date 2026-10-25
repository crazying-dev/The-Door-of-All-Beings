# 客户端网络层（code/net）

服务端协议见 [server/protocol.md](../../../server/protocol.md)（4 行竖线报文 + 单行长响应 + 10Hz PUSH）。

| 文件 | 类型 | 职责 |
|---|---|---|
| `net_config.gd` | RefCounted | 服务端地址的唯一来源：环境变量覆盖 + 生产默认值 |
| `tcp_client.gd` | Node | 协议层：建连、组包、收帧、串行化请求、超时、往返耗时统计。**不懂业务** |
| `game_api.gd` | Node | 业务层：hello / device / register / login / state / item / world，并维护本机设备、当前账号、玩家身份与延迟 |
| `net_sync.gd` | Node | 世界场景的同步层：10Hz 上报 + 消费 PUSH world / event + 远端玩家代理 |

## 启动流程

```
StartCreen.tscn / User(userDOM.gd)
  → GameApi.connect_and_hello()      握手，拿服务端版本 / push_hz / user_name_max_len
  → GameApi.ensure_device()          本地有 hex 就“确认”，没有就申一张（服务端签发）
  → 本地有账号 ? login(user_id) : register(user_name, race)
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

## 与服务端 v2（库权威版）的对齐点

服务端本次是**向后兼容扩展**，没有升协议号（仍为 1.1）：

| 变化 | 客户端对应 |
|---|---|
| `register` 支持 `&<race>` | `game_api.register(name, race)`，缺省读 `AccountStore.read_race()`（默认 `human`） |
| `state` 扩到 8 段（hp / state / stamina / mana） | `game_api.send_state()` 总是带满；`net_sync` 的三围用 `hp` / `stamina` / `mana` 变量 |
| `state` 响应多了 `rev` | `game_api.state_rev` / `net_sync.state_rev()` |
| `register` / `login` 响应多了 `player_id` / `player_name` / `race` | `game_api.player_id` / `player_name` / `race` |
| `world` 响应多了 `world` 元信息 | `game_api.world_meta` / `net_sync.world_meta_received` 信号 |
| 服务端玩家动作词只有 `idle` / `moving` / `dead` | 上报用 `moving`；渲染时 `moving` / `walk` / `run` 都当走路 |

> 种族目前**只在本机保存**（`user://race.txt`），注册那一刻带给服务端；
> 已注册账号的种族要等服务端加动作才能改。

## F3 调试面板的帧率 / 延迟

- **帧率**：`Engine.get_frames_per_second()`（上一秒的平均值），`world.gd` 每 0.1s 刷一次。
- **延迟**：不是单独发 `ping`，而是直接量**每次请求的往返耗时**——
  `tcp_client` 在发车前记 `Time.get_ticks_usec()`，收到对应响应时算差值，
  `last_rtt_ms` 是原始值、`avg_rtt_ms` 是指数平滑值（`RTT_SMOOTH = 0.25`）。
  登录后 10Hz 的 `state` 请求就是天然的心跳，所以读数一直在跳。
  未登录 / 无样本时为 `-1`，HUD 显示「延迟 —（未连接）」。

> 等响应是 `await get_tree().process_frame` 轮询的，所以读数自带 1 帧的量化误差
> （60fps 下约 16ms）。看趋势够用，不要拿它去比对毫秒级的服务端日志。

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
  （不然按 1/2/3 也会切它们的动画），位置是插值过去的，动作按 `state` 字段切 walk / idle
  （`moving` / `walk` / `run` → walk，其余 → idle）。

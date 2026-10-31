# 客户端网络层（code/net）

服务端协议见 [server-socket/protocol.md](../../../server-socket/protocol.md) 与 [server-socket/flows.md](../../../server-socket/flows.md)
（协议 **v1.3**：4 行竖线报文 + **联合哈希** + **批量多 action** + **拉取式**（服务端不再推送）+ 会话 Token 随 hello 下发）。

| 文件 | 类型 | 职责 |
|---|---|---|
| `net_config.gd` | RefCounted | 服务端地址的唯一来源：HTTP 发现域名 / TCP 直连回退 + 环境变量覆盖 |
| `app_info.gd` | RefCounted | 客户端版本 / 平台 / 语言（**每条请求的第 1 行都带**） |
| `tcp_client.gd` | Node | 协议层：建连、组包、联合哈希、收帧、串行化请求、批量请求、超时、往返耗时、**会话 Token 自动携带**。**不懂业务** |
| `game_api.gd` | Node | 业务层：hello / device / check_name / register / login / state / peers / world / item，维护本机设备、当前账号、玩家身份、存档出生点与延迟 |
| `net_sync.gd` | Node | 世界场景的同步层：**一条批量报文**同时上报 state + 拉 peers + 远端玩家代理 + **断线重连** |

## 启动流程

```
StartCreen.tscn / User(userDOM.gd)
  → GameApi.connect_and_hello()
        ① HTTP GET /api/v1/endpoint 发现 TCP 游戏服地址（失败才回退 NetConfig.host()/port()）
        ② TCP 建连（建连后服务端**不发任何帧**，不要等 PUSH）
        ③ 首个 hello：取 server_version / protocol_version / user_name_max_len / session_token
  → AccountStore.probe_device_integrity()   本地 .mac 验签；不过 → 弹窗征得同意才删残留文件
  → GameApi.ensure_device()                本地有 hex 就“确认”，没有就申一张（服务端签发）
  → 本地有账号 ? login(user_id) : check_name(name) → register(name, race, player_id)
  → 进入下一场景（race.tscn → lobby.tscn 门外大厅，纯离线；穿光柱换到 inner_main.tscn）
        → 进门内世界时 net_sync.setup(player, inner_main)
        → GameApi.login_with_saved_account()   重新建连并静默登录
        → 先按 login 回写的存档坐标落位（pos_x/y/z/yaw），**再**开上报，否则首帧会覆盖存档点
        → _process 每 0.1s 一轮批量：第 2 行 = `state | <8 段>;;peers | `，服务端按序回两帧
        → 意外断线：5s 宽限期内自动重连 + 重登，失败就静默降级单机
```

## 身份与 Token

- 登录前：第 3 行带设备标识（`<device> | <user_id>`），服务端靠它认人 / 建会话。
- 登录后：身份记在会话上，后续请求不再重复传 2KB 的 hex。
- 会话 Token **随首个 hello 响应下发**（v1.3 取消建连推送），`tcp_client` 采纳后自动写进第 3 行
  （此时变 3 段：`<token> | <device> | <user>`）；失效时 `game_api._call()` / `call_batch()`
  会自动重登并重试**一次**（服务端校验在 dispatch 之前，重试无副作用）。
- `SESSION_TOKEN_EXPIRED` 不再附推 `reset_token`：客户端要自己重新 `hello` + `login`（本层已自动）。

## 本地联调

```bat
:: 客户端（窗口内执行，或直接写进启动器的环境）
set TDOB_SERVER_HOST=127.0.0.1
set TDOB_SERVER_PORT=5423
:: 可选：让客户端去本地发现接口问 TCP 地址
set TDOB_API_BASE=http://127.0.0.1:4231
:: 服务端（server-socket/ 目录，用内存后端，不碰 PostgreSQL）
set DB_BACKEND=memory
uv run main.py serve
```

## 与服务端 v1.3 的对齐点

服务端这次是**升了协议号的不兼容变更**：`SUPPORTED_VERSIONS = 1.0 / 1.1 / 1.2 / 1.3`，
但哈希算法**按客户端声明的 version 选**（`uses_combined_hash`）——
**1.3 客户端打 1.2 服务端会直接回 `BAD_PLAIN_HASH`**，两端必须同时部署。

| 变化 | 客户端对应 |
|---|---|
| 协议号 `1.3`（`SUPPORTED_VERSIONS` 含 1.0~1.3） | `tcp_client.PROTOCOL_VERSION = "1.3"` |
| 请求尾哈希改**联合哈希** `sha256(第1行 + "\n" + 第2行 + "\n" + 第3行)` | `tcp_client.build_packet()`；逐字节算，**不要 strip** |
| 第 2 行支持**批量多 action** `a1\|d1;;a2\|d2`，按序回多帧 | `tcp_client.request_batch()` + `game_api.call_batch()` + `net_sync._process()` |
| **取消所有服务端推送**（10Hz world / 建连 `PUSH \| token` / join-leave 事件） | 不再订阅 `pushed`；`net_sync` 改轮询 `peers` |
| 会话 Token 改为**随首个 hello 响应下发** | `game_api._adopt_session_token()`；`tcp_client.adopt_token()` |
| `hello` 回 `server_push: false` / `push_hz: 0`（兼容字段） | `game_api.push_hz` 恒为 0，仅供展示 |
| `peers` 拉在线快照（含 `players_rev` / `world_rev` / `t`） | `net_sync._on_peers_result()` → `_apply_players()`；消失即拆代理 |
| `login` 响应新增 `pos_x / pos_y / pos_z / yaw`（上次退出存档点）并回写 `session_token` | `game_api._apply_spawn()` → `net_sync._apply_spawn_to_player()` |
| `device` payload 变 `<fp_hex>&<length>`（硬件指纹 + 票据长度，两段可省），签发不落库 | 客户端目前仍传空（**待办**：接上机器指纹才能让服务端侧也做到换机不可用） |
| 新增动作 `check_name`（注册前查重） | `game_api.check_name()` + `userDOM._name_available()`；回 `UNKNOWN_ACTION` 时直接放行 |
| `register` payload 为 `<user_name>&<race>&<player_id>` | `game_api.register()` 带本机 UUID（`AccountStore.ensure_player_id()`） |
| `login` payload 为 JSON `{"player_id","file_hash"}` | `game_api._login_no_retry()`；`player_id` 用服务端 `user_id`，`file_hash` 默认不传 |
| `device` / `check_name` 是纯读，不再 `touch_device` | 客户端可放心重复调用（不会钩 `DUPLICATE_REQUEST`） |
| 意外断开有 `GRACE_SECONDS`（默认 5s）宽限期 | `net_sync.RECONNECT_WINDOW = 5.0` 内重连 + 重登 |
| `state` 扩到 8 段（hp / state / stamina / mana） | `game_api.state_payload()`（静态集中维护格式），`net_sync` 带满 |
| `state` 响应多了 `rev` | `game_api.apply_state_response()` / `net_sync.state_rev()` |
| 服务端玩家动作词只有 `idle` / `moving` / `dead` | 上报用 `moving`；渲染时 `moving` / `walk` / `run` 都当走路 |

> 种族目前**只在本机保存**（`user://race.txt`），注册那一刻带给服务端；
> 已注册账号的种族要等服务端加动作才能改。

## 设备标识的本地防篡改（v1.3）

- `user://InstallationUniqueID.bin` 旁边多一份 `.bin.mac`：`HMAC-SHA256(密钥 = 机器指纹 + 盐, ID 原文)`。
- `AccountStore.probe_device_integrity()` 是**只读**自检，返回 `"" / "missing" / "tampered"`。
- `"tampered"` 时**不静默删**：`userDOM._resolve_device_integrity()` 弹窗征得同意后，
  才调 `purge_identity()`（设备 ID + 本地账号一起清，因为两者是绑定关系，只清一半会卡在
  `DEVICE_MISMATCH` / `DEVICE_ALREADY_BOUND`）。拒绝则停在登录页，点“->”可再问一次。
- 旧版留下的裸文件（没有 `.mac`）第一次读取时**就地补签**，不会把老用户洗成重领。

## F3 调试面板的帧率 / 延迟

- **帧率**：`Engine.get_frames_per_second()`（上一秒的平均值），`world_base.gd` 每 0.1s 刷一次。
- **延迟**：不是单独发 `ping`，而是直接量**每次请求的往返耗时**——
  `tcp_client` 在发车前记 `Time.get_ticks_usec()`，收到对应响应时算差值，
  `last_rtt_ms` 是原始值、`avg_rtt_ms` 是指数平滑值（`RTT_SMOOTH = 0.25`）。
  v1.3 起每轮是**一条批量报文**，所以读数就是「上报 + 拉取」这一轮的真实 RTT。
  未登录 / 无样本时为 `-1`，HUD 显示「延迟 —（未连接）」。

> 等响应是 `await get_tree().process_frame` 轮询的，所以读数自带 1 帧的量化误差
> （60fps 下约 16ms）。看趋势够用，不要拿它去比对毫秒级的服务端日志。

## 几个必须知道的细节

- **请求是串行的**：服务端回帧不带请求 id，并发就会错配；`tcp_client.request()` / `request_batch()`
  用 `_busy` + `await process_frame` 当互斥锁，后来的请求排队。
- **批量不足会补成同一错误码**：报文级错误（token 失效 / 哈希不符 / 版本不认）时服务端只回一帧，
  `_request_batch_once()` 会把缺的补成**那条 FAIL 的 code**（而不是一律 `TIMEOUT`），
  否则上层会把「哈希不符」误当成网络超时。
- **PUSH 不进 `_inbox`**：`PUSH | topic | {...}` 直接走 `pushed` 信号；该信号在 v1.3 已无人订阅，
  仅为兼容 1.2 老服务端而保留。
- **FAIL 帧装不下 `account`**：`DEVICE_ALREADY_BOUND` 时要再调一次 `device`（行③带本机 device）
  才能拿回绑定账号，见 `userDOM._adopt_bound_account()`。
- **`BAD_DEVICE_ID` 自愈**：本地存的 hex 校验不过（换密钥 / 旧版遗留）时，
  `GameApi.ensure_device()` 会自动丢掉本地文件重领一张。
- **`file_hash` 默认不传**：服务端会把首次提交的值**钉在库里**，之后不一致就回
  `FILE_HASH_MISMATCH` 拒登。客户端一升级就会把自己锁在外面，所以 `game_api.file_hash`
  默认空串（空 = 不参与校验）。
- **`logout` 后要清本地 token**：服务端的 `handle_logout` 会把本连接 token 作废，
  客户端不同步清掉，下一条请求就会被回 `SESSION_TOKEN_EXPIRED`。
- **重连要留在 5s 窗口内**：服务端对意外断开的会话有 `GRACE_SECONDS` 宽限期，窗口内登回去
  会话与 player 行都还在；超时后才由服务端扫描线程清理。
- **远端代理**：`net_sync` 实例化 `Scenes/actors/player.tscn`，把 `preview_keys` 关掉
  （不然按 1/2/3 也会切它们的动画），位置是插值过去的，动作按 `state` 字段切 walk / idle
  （`moving` / `walk` / `run` → walk，其余 → idle）。
- **登录界面没有 Label 组件**：场景里那个 waite Label 已被移掉，提示是 `userDOM._build_hint()`
  **用代码**造在屏幕底部（`SystemFont` 取系统中文字体，不依赖 `game/assets/`）；
  它同时 `print` 到日志，改提示文案时两处一致。

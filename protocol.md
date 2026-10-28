# 众生之门 套接字协议 v1.2（GameTCP）

> 实现：`protocol.py`（解析/编码）· `server.py`（监听/帧读取）· `session.py`（长连接、会话 Token、宽限期与推送）· `actions.py`（动作表）
> 业务流程：`flows.md`（注册 / 登录 / 正常下线 / 意外断开宽限期 / 客户端重连）
> 状态：**已实现并通过冒烟测试**（`scripts/tcp_smoke_test.py`，110 项）

---

## 1. 传输层

| 项 | 值 |
|---|---|
| 协议 | TCP，**长连接**（同一条连接可以连续发多条请求，并持续收推送） |
| 端口 | `.env` 的 `TCP_PORT`（默认跟随 `PORT`，即 **5423**）；可通过 HTTP 发现服务 `GET /api/v1/endpoint` 查询 |
| 编码 | UTF-8 文本，`\n` 为唯一帧边界 |
| 单行上限 | `MAX_LINE_BYTES`（默认 16384 字节），超限 → `TOO_LARGE` |
| 空闲超时 | `READ_TIMEOUT`（默认 180 秒）无任何上行数据即断开 |
| 并发上限 | `MAX_CONNECTIONS`（默认 256），满了直接回 `TOO_MANY_CONNECTIONS` 并关连接 |
| 断线宽限期 | `GRACE_SECONDS`（默认 **5 秒**）：已登录连接意外断开后，玩家仍算在线，期内重连可接管旧会话（见 §3.2） |

### 1.1 为什么要长连接

登录后服务端以 `PUSH_HZ`（默认 **10Hz**）主动下发世界快照。
每次请求新建连接的老模型没法推送，而且每次都要重做一次 device_id（1024 字节）校验，浪费。

### 1.2 客户端连接顺序（推荐）

```
connect TCP
  ← PUSH | token                  服务端立刻下发会话 Token（客户端必须存下来）
  → hello                         握手，拿服务端版本 / 推送频率 / 动作表
  → device                        （本地没有 device_id 时）签发，只回票据不落库
  → device（带 device_id）         确认设备，拿回已绑定账号（可能为 null）
  → account 存在 ? login : check_name → register
  → 进入世界，开始 0.1s 上报 state，同时收 PUSH world
```

此后**每一条请求的第 3 行都带上 token**（见 §2.2）。丢了 token 或拿了上一条连接的
旧 token，请求会被 `SESSION_TOKEN_EXPIRED` 拒掉，同时收到 `PUSH | reset_token`，
客户端应重新走一次 login（详见 flows.md 流程 4）。

---

## 2. 请求报文（4 行）

每行以 `\n` 结尾，第 4 行之后的 `\n` 就是帧边界（**不要**额外发空行）。

```
TheDoorOfBings | 1.2 | <ts> | <encryption> | <client_version> | <platform> | <locale>
<action> | <action_data>
<session_token> | <device_id_hex> | <user_id>
<plain_hash> | <cipher_hash>
```

### 2.1 请求头（第 1 行）

| 字段 | 说明 |
|---|---|
| 固定魔头 | `TheDoorOfBings`，不符 → `BAD_MAGIC` |
| version | `1.2` / `1.1` / `1.0`（1.0 是老格式，头行只有前 4 个字段，仍然接受） |
| ts | Unix 秒级时间戳；与服务端偏差 > `REQUEST_TIME_WINDOW`（默认 **300s**）→ `REQUEST_EXPIRED` |
| encryption | 目前仅 `none` |
| client_version | **客户端软件版本**，例 `0.1.0`。每个请求都要带，服务端记进 `devices.client_version` |
| platform | `windows` / `linux` / `android` / `web` … |
| locale | 例 `zh_CN` |

> 第 5–7 字段可以整体省略（按空串处理），但**不要少写前 4 个**。

### 2.2 正文（第 2、3 行）

- **第 2 行**：`<action> | <action_data>`，`action_data` 内多参数用 `&` 分隔
- **第 3 行**：`<session_token> | <device_id_hex> | <user_id>`，三段都可为空串

| 字段 | 说明 |
|---|---|
| `session_token` | 本连接专属的会话 Token，**建连时由服务端主动推**（`PUSH \| token`），32 位 hex；`expire=180` 只是参考值，token 实际随连接存活（见 §3.2）。与本会话持有的不一致 → `SESSION_TOKEN_EXPIRED`；形状不对（非 32 位 hex）→ `BAD_SESSION_TOKEN` |
| `device_id_hex` | `device_id.py` 输出 bytes 的 `.hex()`。1024B → **2048 字符**，2048B → 4096 字符。非空时服务端做 HMAC 校验，不过 → `BAD_DEVICE_ID` |
| `user_id` | 形如 `ZWME-2WKQ`。带了合法 token 时只在「与本连接已登录账号不一致」时拒；没带 token 则校验「该账号确实绑定在本设备上」 → `BAD_USER_ID` |

**向后兼容**：第 3 行只写 2 段（`<device_id_hex> | <user_id>`）仍被接受，服务端按「没带 token」
处理，退回 v1.1 的身份规则（不校 token，带 `user_id` 时查库确认绑定）。

**登录完成后**（`login` 成功），后续请求可以把 `device_id_hex` / `user_id` 都留空——身份已记在会话上。
（带 `user_id` 就必须同时带 `device_id`；带上了合法 token 则两者都可省，省掉 2KB 的 device_id。）

### 2.3 请求尾（第 4 行）

```
plain_hash | cipher_hash
```

- `plain_hash = sha256(第2行原文 + "\n" + 第3行原文)` 的 hex（64 字符小写）
  （**第 3 行按你实际发出的内容算**：带 token 就是 3 段，不带就是 2 段）
- **逐字节计算，不要 strip / 不要 trim 行尾空格**
  （v1.0 的老服务端先 strip 再算，客户端多打一个空格就永远对不上）
- `encryption=none` 时 `cipher_hash == plain_hash`
- 不符 → `BAD_PLAIN_HASH` / `BAD_CIPHER_HASH`

### 2.4 参考实现（GDScript）

```gdscript
func build_packet(action: String, data: String, token: String, device_hex: String, user_id: String) -> PackedByteArray:
	var ts := int(Time.get_unix_time_from_system())
	var head := "%s | 1.2 | %d | none | %s | %s | %s" % [MAGIC, ts, client_version, platform, locale]
	var body1 := "%s | %s" % [action, data]
	# token 为空时退回 v1.1 的 2 字段写法，服务端自动兼容
	var body2 := "%s | %s | %s" % [token, device_hex, user_id] if token != "" else "%s | %s" % [device_hex, user_id]
	var ctx := HashingContext.new()
	ctx.start(HashingContext.HASH_SHA256)
	ctx.update((body1 + "\n" + body2).to_utf8_buffer())
	var digest := ctx.finish().hex_encode()
	return (head + "\n" + body1 + "\n" + body2 + "\n" + digest + " | " + digest + "\n").to_utf8_buffer()
```

> `token` 来自建连后收到的 `PUSH | token` 帧，收到就存进变量，后续每条请求都带上。

---

## 3. 响应 / 推送（单行）

```
OK   | <action> | <json>
FAIL | <action> | <code> | <message>
PUSH | <topic>  | <json>
```

- 所有协议级错误也走 `FAIL`，`<action>` 位置填 `-`（**不再返回裸文本**）
- `<message>` 是给人看的，**可能含中文与空格**，客户端只能凭 `<code>` 分支
- `<json>` 是紧凑 JSON（无空格、无换行），中文不转义

### 3.1 错误码

| code | 含义 | 客户端建议处理 |
|---|---|---|
| `BAD_MAGIC` | 魔头不符 | 代码写错了 |
| `BAD_VERSION` | 协议版本不支持 | 提示升级客户端 |
| `REQUEST_EXPIRED` | 时间戳偏移过大 | 校正本机时间后重试 |
| `DUPLICATE_REQUEST` | 重复报文（同一会话 + 同一秒 + 同一正文） | 忽略，不要重试 |
| `BAD_ENCRYPTION` | 加密方式不支持 | 提示升级 |
| `BAD_PLAIN_HASH` / `BAD_CIPHER_HASH` | 哈希不对 | 代码写错了，检查是否 strip 过 |
| `MALFORMED` | 结构不对（行数/段数/UTF-8） | 代码写错了 |
| `TOO_LARGE` | 单行超长 | 检查 device_id 是否异常 |
| `BAD_DEVICE_ID` | 设备 ID 非法（伪造/损坏/过期） | 丢弃本地 ID，重新 `device` |
| `BAD_USER_ID` | user_id 非法或不匹配 | 回登录页 |
| `UNKNOWN_ACTION` | 动作不存在 | 代码写错了 |
| `BAD_PAYLOAD` | 动作参数格式不对 | 代码写错了 |
| `NOT_AUTHENTICATED` | 未登录就调需要登录的动作 | 先 `login` |
| `NOT_READY` | 服务端未就绪（如密钥未配置） | 稍后重试 / 提示运维 |
| `NAME_TAKEN` | 用户名称已被占用 | 换名字 |
| `USER_NOT_FOUND` | 账号不存在 | 回注册页 |
| `DEVICE_MISMATCH` | 账号不属于本设备 | 提示换回原机器 |
| `DEVICE_ALREADY_BOUND` | 本设备已绑定账号 | **再调一次 `device`** 取回 account |
| `ACCOUNT_LOCKED` / `ACCOUNT_DISABLED` | 账号被锁 / 停用 | 提示联系管理员 |
| `FILE_HASH_MISMATCH` | 登录时带的 `file_hash` 与库里不一致（本地文件被改） | 提示校验客户端文件 |
| `BAD_SESSION_TOKEN` | 会话 Token 格式非法 | 代码写错了，检查是否原样回传 |
| `SESSION_TOKEN_EXPIRED` | Token 与本会话不一致（已失效 / 拿的是上一条连接的） | 清掉本地 token，重新 `login` |
| `INTERNAL_ERROR` | 服务端内部异常 | 稍后重试 |
| `TOO_MANY_CONNECTIONS` | 连接数已满 | 退避重连 |

### 3.2 会话 Token 与宽限期（v1.2 新增）

**签发**：每条 TCP 连接建立后，服务端立刻主动推一帧（不等客户端请求）：

```
PUSH | token | {"token":"<32 hex>","expire":180}
```

- 客户端必须把它存下来，并放进**后续每一条请求的第 3 行**
- token 不写库、不落盘，只对当前这条连接有效，进程重启即作废
- `login` 成功响应里也会重复回写一份 `session_token`，便于客户端补存

**校验**：`req.session_token` 非空且与本会话持有的不相等 → 同时做两件事：

```
FAIL | <action> | SESSION_TOKEN_EXPIRED | Session token mismatch, ...
PUSH | reset_token | {"reason":"expired","action":"<被拒的动作>"}
```

客户端收到 `reset_token` 应清空本地 token，重新走 `login`（而不是重试原动作）。

**宽限期**：已登录的连接意外断开（未调 `logout`）时，服务端不立即销毁玩家：

| 情况 | 服务端行为 |
|---|---|
| `GRACE_SECONDS` 内同设备重连并 `login` | 接管旧会话（取消挂起项），位置/状态不丢，旧 token 作废 |
| `GRACE_SECONDS` 超时 | `online=False` → 落库 → 销毁热缓存 → 广播 `PUSH \| event leave` |
| 客户端主动调了 `logout` | 当场完整拆除，**不走宽限期** |

> 完整业务描述见 `flows.md` 流程 3 / 4 / 5。

---

## 4. 动作表

| action | action_data | 需要登录 | 说明 |
|---|---|---|---|
| `ping` | 空 | 否 | 心跳/连通性测试 |
| `hello` | 空 | 否 | 握手：服务端版本、推送频率、动作表 |
| `device` | `<fp_hex>&<length>` | 否 | 签发 / 确认设备 ID（见 §5）。**新签发不写库**，到 `register` 才落库 |
| `check_name` | `<user_name>` | 否 | 注册前名称查重，回 `{"name","available","reason"}` |
| `register` | `<user_name>&<race>&<player_id>` | 否 | 注册，`user_id` 由**服务端**生成；后两段可省（`race` 缺省 `human`，`player_id` 为客户端 UUID） |
| `login` | JSON `{"player_id":"<user_id>","file_hash":"<hex>"}` | 否 | 登录。旧写法兼容：非 JSON 时整段当 `user_id`（不参与文件校验） |
| `logout` | 空 | 否 | 正常下线：**完整拆除**（停推送 → 置离线 → 落库 → 销毁 → 作废 token → 广播 leave） |
| `whoami` | 空 | 否 | 当前会话账号信息 |
| `state` | `<x>&<y>&<z>&<yaw>[&hp][&state][&stamina][&mana]` | **是** | 上报自身位置/状态 |
| `peers` | 空 | **是** | 拉一次在线玩家快照 |
| `world` | 空 | 否 | 拉一次世界元信息 + 全部物品快照 |
| `item` | `<id>&[kind]&<x>&<y>&<z>&<yaw>[&state]` | **是** | 增/改一个世界物品 |

`state` 的 `state` 取值：`idle` / `moving` / `dead`（其他值归为 `idle`）。
坐标绝对值上限 `100000`（非法坐标回 `BAD_PAYLOAD`）。

后四段全部**可省略**，省略就保持原值：

| 段位 | 字段 | 范围 | 说明 |
|---|---|---|---|
| 5 | `hp` | `0 .. hp_max` | 当前生命值，超出会被夹回去 |
| 6 | `state` | 见上 | 与 v1.1 位置一致（放中间是为了不破坏老报文） |
| 7 | `stamina` | `0 .. stamina_max` | 当前体力 |
| 8 | `mana` | `0 .. mana_max` | 当前灵力 |

> 三围上限（`hp_max` / `stamina_max` / `mana_max`）默认都是 100，**客户端目前不能改**（将来由 `admin` 或成长系统改）。
> 每次上报都会让该玩家的 `rev += 1`，响应里的 `rev` 就是回写给客户端的行修订号。

`state` 响应：

```
OK | state | {"t":1761379200,"seq":42,"rev":7}
```

`world` 响应（新增 `world` 元信息）：

```
OK | world | {"world":{"id":"default","name":"众生之门","seed":0,"rev":5},
              "items":[{"id":"door_01","kind":"door","pos_x":0,"pos_y":0,"pos_z":-8,
                        "yaw":90,"state":"idle"}],
              "world_rev":5,"t":1761379200}
```

`register` 响应（`player_id` 未传时退化为 `user_id`）：

```
OK | register | {"user_id":"ZWME-2WKQ","user_name":"勇者",
                  "player_id":"a3f1c8d2-…","player_name":"勇者","race":"elf",
                  "server_version":"3.0.0","protocol_version":"1.2"}
```

`login` 响应（新增 `session_token`）：

```
OK | login | {"user_id":"ZWME-2WKQ","user_name":"勇者",
              "player_id":"a3f1c8d2-…","player_name":"勇者","race":"human",
              "push_hz":10,"session_token":"9f2c…(32 hex)",
              "server_version":"3.0.0","protocol_version":"1.2"}
```

`logout` 响应：

```
OK | logout | {"authenticated":false,"saved":true,"user_id":"ZWME-2WKQ"}
```

> `login` 的 `file_hash` 三态：**不传 / 非 JSON payload** ⇒ 不参与文件校验；
> 传空串 ⇒ 与库里已有值不等就拒（`FILE_HASH_MISMATCH`）；传非空 ⇒ 库里为空则记下来，
> 已有且不等则拒。重连时 `player` 行不在内存时会自动从库重载（位置不丢）。

> `state` / `world` / `item` 的可选段与新增字段都不改版本号，老客户端不改也能跑；
> 但 **v1.2 的会话 Token 是新增字段，第 3 行少了它就按 v1.1 处理**。

---

## 5. `device`：签发 / 确认设备 ID

设备 ID 是**服务端签发**的无状态凭证（客户端**不持密钥**）。
结构：`[ver(2)][lenFlag(2)][ts(4)][nonce(32)][fp(16)][tag(32)][HKDF 扩展]`，总长 1024 或 2048 字节。

- `tag = HMAC-SHA256(DEVICE_ID_SECRET, ver|lenFlag|ts | nonce | fp)`
- 改任意一个字节（含末尾扩展段）都会校验失败
- 校验**不查库**，靠自身 HMAC 即可

### 5.1 两种用法

| 第 3 行 device_id 段 | 行为 | 返回 |
|---|---|---|
| **空** | 签发一张新 ID，**不写库** | `device_id`（新的）、`known=false`、`stored=false` |
| **非空且合法** | **不重新签发、不写库**，只回读该设备绑定的账号 | `device_id`（原样）、`known=true`、`stored=true`、`account` |

```
OK | device | {"device_id":"ab12…","length":1024,"issued_at":1761379200,
                "known":true,"stored":true,"account":{"user_id":"ZWME-2WKQ","user_name":"勇者"},
                "server_version":"3.0.0","protocol_version":"1.2"}
```

`account` 为 `null` 表示这台设备还没绑账号 → 走 `check_name` + `register`。

> v1.2 调整了旧行为：**`device` 不再写 `users` 表**（新机阶段根本没有账号，
> 提前落库会留一堆永不可用的孤行）。设备行只在 `register` 里首次 `touch_device`。

> **客户端务必把返回的 `device_id` 原样存到本地**（`user://`）。
> 每次调 `device` 不带 ID 都会拿到一张**不同的**新 ID（nonce 随机），
> 拿旧的 ID 去登录新设备是绑不上的。

---

## 6. 推送帧

服务端会主动推的 topic 共 4 个：`token` / `reset_token`（见 §3.2）、
`world`（§6.1）、`event`（§6.2）。**读响应时必须跳过 `PUSH` 帧**（见 §9）。

### 6.1 `PUSH | world | <json>`（`PUSH_HZ` 默认 10Hz）

```json
{
  "t": 1761379200.123,
  "seq": 42,
  "players_rev": 7,
  "players": [
    {"user_id":"ZWME-2WKQ","user_name":"勇者","player_name":"勇者","race":"human",
     "pos_x":12.5,"pos_y":0.0,"pos_z":-8.0,
     "yaw":90.0,"state":"moving",
     "hp_max":100.0,"hp":100.0,
     "stamina_max":100.0,"stamina":100.0,
     "mana_max":100.0,"mana":100.0}
  ],
  "online": 1
}
```

`items` 与 `world_rev` **只在变化后附带一次**（客户端 `world_rev` 落后时才需要全量物品）：

```json
{ "…": "…", "world_rev": 3, "items": [{ "id":"door_01","kind":"door","pos_x":0,"pos_y":0,"pos_z":-8,"yaw":90,"state":"idle" }] }
```

### 6.2 `PUSH | event | <json>`（玩家上下线）

```json
{"kind":"join","user_id":"ZWME-2WKQ","user_name":"勇者","t":1761379200}
{"kind":"leave","user_id":"ZWME-2WKQ","user_name":"勇者","t":1761379260}
```

自己上下线不会推给自己。
宽限期超时销毁、`logout` 完整拆除时都会广播 `leave`（与正常下线同形，客户端不需要区分）。

---

## 7. 完整往返示例

```
S→C  PUSH | token | {"token":"9f2c…(32 hex)","expire":180}
     （建连后服务端立刻推，不等请求）

C→S  TheDoorOfBings | 1.2 | 1761379200 | none | 0.1.0 | windows | zh_CN
     hello | 
     9f2c…(token) |  |
     <64hex> | <64hex>

S→C  OK | hello | {"magic":"TheDoorOfBings","server_version":"3.0.0","protocol_version":"1.2",
                    "push_hz":10,"autosave_interval":60,"device_id_length":1024,
                    "user_name_max_len":24,"actions":["check_name","device","hello","item",
                    "login","logout","peers","ping","register","state","whoami","world"],
                    "now":1761379200}

C→S  … device | 
     9f2c…(token) |  |
     …

S→C  OK | device | {"device_id":"ab12…(2048 hex)","length":1024,"issued_at":1761379200,
                     "known":false,"stored":false,"account":null,
                     "server_version":"3.0.0","protocol_version":"1.2"}

C→S  … check_name | 勇者
     9f2c…(token) | <device_hex> | 
     …

S→C  OK | check_name | {"name":"勇者","available":true,"reason":""}

C→S  … register | 勇者&elf&a3f1c8d2-…
     9f2c…(token) | <device_hex> | 
     …

S→C  OK | register | {"user_id":"ZWME-2WKQ","user_name":"勇者",
                       "player_id":"a3f1c8d2-…","player_name":"勇者","race":"elf",
                       "server_version":"3.0.0","protocol_version":"1.2"}

C→S  … state | 12.5&0&-8&180&77&moving&33&44
     9f2c…(token) |  |
     …

S→C  OK | state | {"t":1761379200,"seq":2,"rev":2}

S→C  PUSH | event | {"kind":"join","user_id":"ZWME-2WKQ","user_name":"勇者","t":1761379200}
S→C  PUSH | world | {"t":1761379200.1,"seq":1,"players_rev":1,"players":[…],"online":1}

C→S  … logout | 
     9f2c…(token) |  |
     …

S→C  OK | logout | {"authenticated":false,"saved":true,"user_id":"ZWME-2WKQ"}
```

> 登录后面的请求里 `device_id` 都留空了：带上了合法 token 就不必再发 2KB 票据。
> `register` / `login` 之前必须带 `device_id`（服务端要拿它绑账号）。

---

## 8. HTTP 旁路（只做发现，已拆到独立组件）

游戏通信全走 TCP，HTTP 只回答「客户端该连哪里」。
本组件（server-socket）**不提供 HTTP 接口**，发现接口由独立的 **server 组件**
（仓库 `server` 分支，默认监听 4231）承担，路径与响应形状不变：

| 路径 | 说明 |
|---|---|
| `GET /` | 服务名与版本 |
| `GET /api/v1/endpoint` | `{"tcp":{"host":"…","port":5423},"protocol_version":"1.2","service_version":"3.0.0","transport":"tcp","encoding":"utf-8"}` |
| `GET /game/api` | 同上（旧路径，过渡期保留） |
| `GET /healthz` | 健康检查（仅 HTTP 服务自身，不含游戏服状态） |

`host` / `port` 来自 server 组件的 `.env`（`HOST` / `PORT`），需与本组件的实际监听地址一致。

> ⚠️ 下发版本号写死在 server 组件的 `config.py`（`PROTOCOL_VERSION` / `SERVICE_VERSION`），
> 该分支仍是 `1.1` / `2.0.0`，**需要跟着改成 `1.2` / `3.0.0`**，否则客户端会拿到旧的兼容提示。
> 协议本身向后兼容（v1.2 客户端不拿 token 也能跑 v1.1 报文），不影响连入。

---

## 9. 已知限制 / TODO

1. **没有 nonce 字段**：防重放目前只能在「同一会话 + 同一秒 + 同一正文」内去重
   （`server.py::ReplayGuard`），跨连接重放挡不住。会话 Token 只标识连接、
   不防重放（拿到同一连接的 token 就能重放）。等上加密时一起加 nonce（v1.3）。
2. **无 `encryption` 实现**：`cipher_hash` 目前恒等于 `plain_hash`，链路是明文。
   token 也是明文发的，生产环境要么走内网要么等加密层。
3. **慢客户端会拖广播**：`Session.send` 是同步 `sendall`，10Hz 广播线程可能被单个卡住的连接拖慢。
   生产量大时应改成「每会话发送队列 + 丢帧策略」。
4. **`device_id` 每次请求占 2KB**：带上合法 token 后，第 3 行的 `device_id` / `user_id` 都可以留空。
5. **`register` 的 FAIL 帧装不下 `account`**：设备已绑定时客户端需要再调一次 `device` 取回。
   将来可给 `FAIL` 加一列 JSON（v1.3）。
6. **token 不续期**：`expire=180` 只是给客户端的参考值，服务端实际只在连接存活期间持有它；
   长时间只收推送不发上行时需要靠 `ping` 撑住 `READ_TIMEOUT`。将来可改成定期重推 token。
7. **读响应必须跳过 `PUSH` 帧**：10Hz 世界快照随时插在响应前面。
   参考 `scripts/tcp_smoke_test.py::read_frame`（`kind == "PUSH"` 就继续读下一行）；
   只读一行的客户端会把 PUSH 当响应，然后抱怨「响应里没有 `rev`」。

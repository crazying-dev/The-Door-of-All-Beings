# 众生之门 套接字协议（GameAPI）

> 实现：服务端 `main.py::GameAPI.get_data`，动作实现 `actions.py`
> 状态：草案 v1.0

## 1. 传输层

| 项 | 值 |
|---|---|
| 协议 | TCP |
| 端口 | `.env` 的 `PORT`（默认 **5423**），可通过 HTTP `POST /game/api` 查询 |
| 地址 | `/game/api` 返回的 `host`（公网 IP） |
| 编码 | UTF-8 文本，一次请求 = 一次连接（收包→处理→回写→断开） |

## 2. 报文结构

报文共 **4 行**（空行会被忽略，行内 `|` 两侧空白不参与语义）：

```
TheDoorOfBings | <version> | <UTCtime> | <encryption_method>    # 请求头
<action> | <action_data>                                        # 正文①
[<device_id>] | [<userID>]                                      # 正文②
<plain_hash> | <cipher_hash>                                    # 请求尾
```

### 2.1 请求头

| 字段 | 说明 |
|---|---|
| 固定魔头 | `TheDoorOfBings`，不符 → `Invalid protocol magic header` |
| version | 协议版本，当前仅支持 `1.0` |
| UTCtime | Unix 秒级时间戳，与服务器时间偏差 > **300s** → `Request expired`（防重放） |
| encryption_method | 当前仅支持 `none` |

### 2.2 正文

- **行①** `action`：动作名（见 §4）；`action_data`：动作参数，多参数用 `&` 分隔
- **行②** `device_id` / `userID`：方括号表示可为空串。
  - `device_id` 为 **hex 字符串**（对应 `device_id.py` 生成 bytes 的 `.hex()`，1024B→2048 字符 / 2048B→4096 字符）
  - 非空时服务端做密码学校验，不过 → `Invalid Device ID`
  - `userID` 非空时校验「账号存在且与该设备绑定」，不过 → `Invalid User ID`

> ⚠️ 行②中的 `|` 会破坏拆分，device_id/userID 本身是十六进制/ID 字符集，不受影响；自定义字段需避免 `|` 与换行。

### 2.3 请求尾

对**正文两行原文**（行①`\n`行②，即客户端发送的原始两行、含其间换行、不含行尾换行）计算：

| 字段 | 说明 |
|---|---|
| plain_hash | `sha256(正文).hexdigest`，不符 → `Invalid plain hash` |
| cipher_hash | 密文哈希；`encryption_method=none` 时密文即明文，值与 plain_hash 相同，不符 → `Invalid cipher hash` |

## 3. 响应格式

成功：

```
OK | <action> | <JSON结果>
```

动作级失败（协议合法但业务失败）：

```
FAIL | <action> | <错误描述>
```

协议级失败：直接返回单行错误文本（见 §5）。

## 4. 动作定义

| action | action_data | 结果 JSON / 失败原因 |
|---|---|---|
| `ping` | （任意，忽略） | `{"pong_at": <服务器秒级时间戳>}` |
| `register` | `<user_name>&<user_id>` | 成功 `{"user_id", "user_name"}`；要求 device_id 非空且合法；重名/重复ID → `Username or user id already exists` |
| `login` | `<user_id>`（留空则取行②的 userID） | 成功 `{"user_id", "user_name"}`；账号与设备不匹配 → `User not found or device mismatch`；锁定/禁用有独立提示 |

**新增动作**：在 `actions.py` 中用 `@action("名字")` 注册即可，签名固定为
`handler(payload: str, device_id_hex: str, user_id: str) -> str`。

## 5. 错误码表

| 触发条件 | 返回文本 |
|---|---|
| 魔头错误 | `Invalid protocol magic header` |
| 版本不支持 | `Unsupported protocol version` |
| 请求超时/重放 | `Request expired` |
| 加密方式不支持 | `Unsupported encryption algorithm` |
| 明文哈希无效 | `Invalid plain hash` |
| 密文哈希无效 | `Invalid cipher hash` |
| 分割字段数量不对 / 非UTF-8 / 行数≠4 | `Malformed packet structure` |
| device_id非法 | `Invalid Device ID` |
| user_id非法 | `Invalid User ID` |
| 动作未注册 | `Unknown action` |

## 6. 示例交互

注册（假设 device_id hex 已省略为 `abcd...`）：

```
TheDoorOfBings|1.0|1761379200|none
register|勇者&U10001
abcd...|
<sha256(第2、3行)>|<同上>
```

响应：

```
OK | register | {"user_id": "U10001", "user_name": "勇者"}
```

## 7. 已知限制 / TODO

- `encryption_method` 目前只有 `none`，cipher_hash 校验为占位；引入加密后需实现真实密文哈希
- 一次请求一次连接，无长连接/心跳；后续实时同步走独立通道
- 防重放仅靠时间窗，nonce 去重表未实现

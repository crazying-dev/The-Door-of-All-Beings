extends Node
## 业务层：把协议动作包成 GDScript 方法，并维护「本机设备 / 当前账号」状态。
##
## 协议 v1.3（规范见 server-socket/protocol.md 与 flows.md）：
##   * 一条 TCP 长连接，服务端**不再主动推送**：世界/在线数据由 net_sync 按自选频率拉取；
##   * 设备标识由**服务端签发**（客户端不持密钥），拿到后原样存在本地；
##   * user_id 由**服务端生成**，注册提交 `<user_name>&<race>&<player_id>`；
##   * player_id 是**本机生成的 UUID**，服务端另存一份做本地标识；
##   * 会话 Token 随首个 hello 响应下发，本层采纳后交给 TcpClient 自动携带；
##   * Token 失效（SESSION_TOKEN_EXPIRED）由 _call 统一重登重试。

const TcpClient = preload("res://code/net/tcp_client.gd")
const NetConfig = preload("res://code/net/net_config.gd")
const AccountStore = preload("res://code/StartCreen/account_store.gd")

## 服务端推送的世界快照（10Hz）。
signal world_pushed(payload: Dictionary)
## 服务端推送的玩家上下线事件。
signal event_pushed(payload: Dictionary)
## 登录状态变化。
signal authenticated_changed(authenticated: bool)

## 本机设备 ID（hex，1024 字节 → 2048 字符）。
var device_hex := ""
## 服务端已签发的设备对应的账号（可能为空，表示这台设备还没注册过）。
var bound_account: Dictionary = {}

var user_id := ""
var user_name := ""
var authenticated := false

## 玩家身份（register / login 响应里回显）。
## `player_id` 是**本机生成的 UUID**（协议 v1.2），服务端拿它做本地标识；
## `player_name` 是游戏内显示名；`race` 是种族 key。
var player_id := ""
var player_name := ""
var race := ""

## 登录校验文件哈希（协议 v1.2 可选字段）。**默认空 = 不参与校验**：
## 服务端会把首次提交的值钉在库里，之后不一致就拒登（FILE_HASH_MISMATCH），
## 客户端一升级就会把自己锁在外面，所以默认不开。
var file_hash := ""

## 最近一次 `state` 响应回写的行修订号（服务端的 player.rev）。
var state_rev := 0
## 上一次 `world` 拉到的世界元信息与修订号（服务端新增的 `world` 字段）。
var world_meta: Dictionary = {}
var world_rev := 0

## hello 拿到的服务端信息。
var server_version := ""
var protocol_version := ""
## v1.3：服务端不再主动推送，固定 0；仅用于展示。
var push_hz := 0.0
var user_name_max_len := AccountStore.USER_NAME_MAX_LEN

## 最近一次失败的友好说明（给 UI 用）。
var last_error := ""

## 底层协议客户端实例。故意不写类型标注（它是自定义脚本，静态类型成 Node 会报找不到成员）。
var _tcp = null


func _ready() -> void:
	_tcp = TcpClient.new()
	_tcp.name = "TcpClient"
	add_child(_tcp)
	_tcp.pushed.connect(_on_pushed)


func tcp():
	return _tcp


func is_connected_to_server() -> bool:
	return _tcp != null and _tcp.is_connected_to_server()


# ---------------------------------------------------------------- 握手

## 建连 + hello。失败时 last_error 有原因。
func connect_and_hello() -> bool:
	last_error = ""
	var host := NetConfig.host()
	var port := NetConfig.port()
	if not await _tcp.connect_to(host, port):
		last_error = "无法连接服务器 %s" % NetConfig.describe()
		return false

	var res: Dictionary = await _call("hello")
	if not bool(res.get("ok", false)):
		last_error = _explain(res, "服务器握手失败")
		return false

	var data: Dictionary = res.get("data", {})
	server_version = str(data.get("server_version", ""))
	protocol_version = str(data.get("protocol_version", ""))
	push_hz = float(data.get("push_hz", 0))
	user_name_max_len = int(data.get("user_name_max_len", user_name_max_len))
	# v1.3：会话 Token 随首个 hello 响应下发，存下后后续请求自动携带
	_adopt_session_token(data)
	return true


## 本地设备：没有就申一张，有就拿去确认（顺带拿回已绑定账号）。
func ensure_device() -> bool:
	last_error = ""
	var cached := AccountStore.read_device_id()
	device_hex = cached
	bound_account.clear()

	var res: Dictionary = await _call("device", "", device_hex)
	if not bool(res.get("ok", false)):
		# 本地存的 ID 已被服务端否掉（换了密钥 / 写坏了）：丢掉重领一张
		if str(res.get("code", "")) == "BAD_DEVICE_ID" and cached != "":
			push_warning("本地设备标识已失效，重新申领")
			AccountStore.clear_device_id()
			device_hex = ""
			res = await _call("device", "", "")
		if not bool(res.get("ok", false)):
			last_error = _explain(res, "无法获取设备标识")
			return false

	var data: Dictionary = res.get("data", {})
	var issued := str(data.get("device_id", "")).strip_edges()
	if issued == "":
		last_error = "服务端未返回设备标识"
		return false
	device_hex = issued
	if issued != cached:
		AccountStore.write_device_id(issued)

	var account: Variant = data.get("account", null)
	if typeof(account) == TYPE_DICTIONARY:
		var dict: Dictionary = account
		if str(dict.get("user_id", "")) != "":
			bound_account = dict
	return true


# ---------------------------------------------------------------- 账号

## 本机 player_id（UUID）：注册时要交给服务端，没有就当场生成并落盘。
func ensure_player_id() -> String:
	var pid := AccountStore.ensure_player_id()
	if pid == "":
		push_warning("无法生成 / 保存本机 player_id，注册将退化为由服务端回填 user_id")
	return pid


## 名称查重（v1.2 新增动作，注册前调）。
## 返回统一响应结构；可用性看 `data.available`，原因看 `data.reason`（taken / invalid）。
## 老服务端没有这个动作时会回 UNKNOWN_ACTION —— 调用方应当当作「查不了，直接进注册」。
func check_name(candidate: String) -> Dictionary:
	return await _call("check_name", candidate.strip_edges(), device_hex)


## 注册。payload 为 `<user_name>&<race>&<player_id>`（第三段是本机 UUID，v1.2）。
## `race` 缺省时用本机保存的种族。
func register(new_user_name: String, new_race := "") -> Dictionary:
	var chosen := new_race.strip_edges()
	if chosen == "":
		chosen = AccountStore.read_race()
	var pid := ensure_player_id()
	var payload := "%s&%s&%s" % [new_user_name, chosen, pid]
	var res: Dictionary = await _call("register", payload, device_hex)
	if bool(res.get("ok", false)):
		var data: Dictionary = res.get("data", {})
		_remember_player(data)
		_adopt_session_token(data)
		_mark_authenticated(str(data.get("user_id", "")), str(data.get("user_name", new_user_name)))
		if race != "":
			AccountStore.write_race(race)
	return res


func login(login_user_id: String) -> Dictionary:
	var res: Dictionary = await _login_no_retry(login_user_id, device_hex)
	if bool(res.get("ok", false)):
		var data: Dictionary = res.get("data", {})
		_mark_authenticated(str(data.get("user_id", login_user_id)), str(data.get("user_name", "")))
	return res


## 真正发 `login` 的那一层（不重试）。
## v1.2 的 payload 是 JSON：`{"player_id", "file_hash"}`；
## 协议里的 `player_id` 在登录语境下就是服务端的 `user_id`（见 flows.md §8）。
## `file_hash` 为空时不写这个键 —— 服务端按「不参与校验」处理。
func _login_no_retry(login_user_id: String, device: String) -> Dictionary:
	var payload := {"player_id": login_user_id.strip_edges().to_upper()}
	if file_hash != "":
		payload["file_hash"] = file_hash
	var res: Dictionary = await _tcp.request("login", JSON.stringify(payload), device, "")
	if bool(res.get("ok", false)):
		var data: Dictionary = res.get("data", {})
		_remember_player(data)
		_adopt_session_token(data)
	return res


## 响应里回显的 `session_token` 存一份（reset 后靠它取回可用值）。
func _adopt_session_token(data: Dictionary) -> void:
	if _tcp == null:
		return
	var token := str(data.get("session_token", "")).strip_edges()
	if token != "":
		_tcp.adopt_token(token)


func logout() -> Dictionary:
	var res: Dictionary = await _call("logout")
	if bool(res.get("ok", false)):
		user_id = ""
		user_name = ""
		player_id = ""
		player_name = ""
		# 服务端 logout 会把本连接的 token 作废；本地必须同步清掉，
		# 否则下一条请求带着旧 token 会被回 SESSION_TOKEN_EXPIRED。
		if _tcp != null:
			_tcp.clear_token()
		set_authenticated(false)
	return res


## 从 register / login 的响应里记下玩家身份（旧版服务端没有这三个字段，缺了就保持原值）。
func _remember_player(data: Dictionary) -> void:
	if data.has("player_id"):
		player_id = str(data.get("player_id", "")).strip_edges()
	if data.has("player_name"):
		player_name = str(data.get("player_name", "")).strip_edges()
	if data.has("race"):
		race = str(data.get("race", "")).strip_edges()


func whoami() -> Dictionary:
	return await _call("whoami")


func _mark_authenticated(new_user_id: String, new_user_name: String) -> void:
	user_id = new_user_id.strip_edges().to_upper()
	user_name = new_user_name.strip_edges()
	set_authenticated(true)
	if user_id != "" and user_name != "":
		AccountStore.write_account(user_name, user_id)


func set_authenticated(value: bool) -> void:
	if authenticated == value:
		return
	authenticated = value
	authenticated_changed.emit(value)


## 用本地账号静默重登（进入世界时用）。成功返回 true。
func login_with_saved_account() -> bool:
	var account := AccountStore.read_account()
	if account.is_empty():
		last_error = "本机没有已保存的账号"
		return false
	if not await connect_and_hello():
		return false
	if not await ensure_device():
		return false
	var res: Dictionary = await login(str(account.get("UserID", "")))
	if not bool(res.get("ok", false)):
		last_error = _explain(res, "登录失败")
		return false
	return true


# ---------------------------------------------------------------- 世界

## 上报自身状态。payload：`<x>&<y>&<z>&<yaw>[&hp][&state][&stamina][&mana]`。
## 后四段都是可选的；本方法总是带满，方便服务端落库三围。
func send_state(
	pos: Vector3,
	yaw_degrees: float,
	hp := 100.0,
	state := "idle",
	stamina := 100.0,
	mana := 100.0,
) -> Dictionary:
	var payload := "%.2f&%.2f&%.2f&%.1f&%.0f&%s&%.0f&%.0f" % [
		pos.x, pos.y, pos.z, yaw_degrees, hp, state, stamina, mana,
	]
	var res: Dictionary = await _call("state", payload)
	if bool(res.get("ok", false)):
		var data: Dictionary = res.get("data", {})
		state_rev = int(data.get("rev", state_rev))
	return res


func fetch_peers() -> Dictionary:
	return await _call("peers")


## 拉世界快照。响应比旧版多了 `world` 元信息（名称 / 种子 / rev）。
func fetch_world() -> Dictionary:
	var res: Dictionary = await _call("world")
	if bool(res.get("ok", false)):
		var data: Dictionary = res.get("data", {})
		world_rev = int(data.get("world_rev", world_rev))
		var meta: Variant = data.get("world", null)
		if typeof(meta) == TYPE_DICTIONARY:
			world_meta = meta
	return res


func upsert_item(item_id: String, kind: String, pos: Vector3, yaw_degrees: float, state := "idle") -> Dictionary:
	var payload := "%s&%s&%.2f&%.2f&%.2f&%.1f&%s" % [
		item_id, kind, pos.x, pos.y, pos.z, yaw_degrees, state,
	]
	return await _call("item", payload)


func ping() -> Dictionary:
	return await _call("ping")


## 网络往返耗时（毫秒，指数平滑）；-1 表示还没测到（未连接 / 无样本）。
func latency_ms() -> float:
	return -1.0 if _tcp == null else float(_tcp.avg_rtt_ms)


## 最近一次往返耗时（毫秒，未平滑）；-1 表示还没测到。
func last_latency_ms() -> float:
	return -1.0 if _tcp == null else float(_tcp.last_rtt_ms)


## 世界名（来自 `world` 动作的元信息），没拿到就返回空串。
func world_name() -> String:
	return str(world_meta.get("name", ""))


## 本连接的会话 Token（协议 v1.2，空串 = 未拿到 / 已作废）。
func session_token() -> String:
	return "" if _tcp == null else str(_tcp.session_token)


# ---------------------------------------------------------------- 内部

## 登录前带上设备（服务端要靠它认人），登录后就不再重复传 2KB 的 hex。
## v1.2：token 由 TcpClient 自动带上；token 失效时这里会自动重登并重试一次。
func _call(action: String, action_data := "", override_device := "") -> Dictionary:
	var device := ""
	if not authenticated:
		device = override_device if override_device != "" else device_hex
	var res: Dictionary = await _tcp.request(action, action_data, device, "")
	if str(res.get("code", "")) != "SESSION_TOKEN_EXPIRED":
		return res
	return await _retry_after_token_reset(action, action_data)


## 拿到 SESSION_TOKEN_EXPIRED：清 token → 重登换新 token → 重试原请求（只重试一次）。
## 服务端的 token 校验在 dispatch 之前，所以被拒的请求没有副作用，重试是安全的。
func _retry_after_token_reset(action: String, action_data: String) -> Dictionary:
	push_warning("会话 Token 已失效，重新登录后重试：%s" % action)
	if _tcp != null:
		_tcp.clear_token()
	var device := device_hex
	if user_id != "" and device != "":
		var relog: Dictionary = await _login_no_retry(user_id, device)
		if not bool(relog.get("ok", false)):
			last_error = _explain(relog, "会话已失效，请重新登录")
			return relog
		device = ""
	return await _tcp.request(action, action_data, device, "")


func _on_pushed(topic: String, payload: Dictionary) -> void:
	match topic:
		"world":
			world_pushed.emit(payload)
		"event":
			if str(payload.get("kind", "")) == "leave" and str(payload.get("user_id", "")) == user_id:
				set_authenticated(false)
			event_pushed.emit(payload)
		"reset_token":
			# 服务端认定本地拿的是旧 Token（flows.md 流程 5）：重登一次换回来
			_relogin_after_token_reset.call_deferred()
		_:
			pass


## token 被服务端作废后重登（拿回新 token + 重新绑定会话）。
func _relogin_after_token_reset() -> void:
	if user_id == "" or device_hex == "":
		return
	var res: Dictionary = await _login_no_retry(user_id, device_hex)
	if bool(res.get("ok", false)):
		print("会话 Token 已重置，重新登录成功")
	else:
		push_warning("会话 Token 重置后重新登录失败：%s" % str(res.get("code", "")))
		set_authenticated(false)


## 供 UI 层调用：把服务端错误码转成一句人话。
func explain(res: Dictionary, fallback: String) -> String:
	return _explain(res, fallback)


## 把服务端错误码转成一句人话。code 一定稳定，message 只当补充。
## 服务端 ERROR_MESSAGES 是英文，所以这里必须自己映射一遍中文。
func _explain(res: Dictionary, fallback: String) -> String:
	var code := str(res.get("code", ""))
	var message := str(res.get("message", "")).strip_edges()
	match code:
		"TIMEOUT", "DISCONNECTED", "NETWORK_ERROR":
			return "无法连接服务器，请检查网络后重试"
		"BAD_VERSION":
			return "客户端版本过旧，请更新后再试"
		"BAD_USER_ID":
			return "账号与本机设备不匹配，请回登录页"
		"BAD_DEVICE_ID":
			return "设备标识已失效，正在重新申领"
		"REQUEST_EXPIRED":
			return "本机时间与服务端相差太大，请校对系统时间"
		"NOT_READY":
			return "服务器正在维护，请稍后重试"
		"TOO_MANY_CONNECTIONS":
			return "服务器人数已满，请稍后重试"
		"NAME_TAKEN":
			return "该用户名称已被占用，请换一个"
		"USER_NOT_FOUND":
			return "账号不存在"
		"DEVICE_MISMATCH":
			return "该账号绑定的不是本机设备，无法登录"
		"DEVICE_ALREADY_BOUND":
			return "本机已绑定其它账号，无法再注册新账号"
		"ACCOUNT_LOCKED":
			return "账号已被锁定，暂时无法登录"
		"ACCOUNT_DISABLED":
			return "账号已停用"
		"FILE_HASH_MISMATCH":
			return "客户端文件校验失败，请重新安装客户端"
		"SESSION_TOKEN_EXPIRED", "BAD_SESSION_TOKEN":
			return "会话已失效，正在重新连接"
		"DUPLICATE_REQUEST":
			return "操作过于频繁，请稍后重试"
		"BAD_PAYLOAD":
			return "请求格式不正确，请更新客户端"
		"INTERNAL_ERROR":
			return "服务器内部错误，请稍后重试"
		"SHUTTING_DOWN":
			return "服务器正在关闭，请稍后重试"
	return message if message != "" else fallback

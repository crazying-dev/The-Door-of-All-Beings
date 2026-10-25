extends Node
## 业务层：把协议动作包成 GDScript 方法，并维护「本机设备 / 当前账号」状态。
##
## 与旧版的区别：
##   * 旧版是「一次请求一次 HTTP」，用完即弃；这里是**一条TCP长连接**，
##     登录后同一连接会持续收到 PUSH world。
##   * 设备标识改由**服务端签发**（客户端不持密钥），拿到后原样存在本地。
##   * user_id 改由**服务端生成**，注册只提交 user_name。

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

## 服务端返回的玩家身份（register / login 响应里的新字段）。
## `player_id` 目前与 user_id 同值；`player_name` 是游戏内显示名；`race` 是种族 key。
var player_id := ""
var player_name := ""
var race := ""

## 最近一次 `state` 响应回写的行修订号（服务端的 player.rev）。
var state_rev := 0
## 上一次 `world` 拉到的世界元信息与修订号（服务端新增的 `world` 字段）。
var world_meta: Dictionary = {}
var world_rev := 0

## hello 拿到的服务端信息。
var server_version := ""
var protocol_version := ""
var push_hz := 10.0
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
	push_hz = float(data.get("push_hz", 10))
	user_name_max_len = int(data.get("user_name_max_len", user_name_max_len))
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

## 注册。payload 为 `<user_name>[&<race>]`；`race` 缺省时用本机保存的种族。
func register(new_user_name: String, new_race := "") -> Dictionary:
	var chosen := new_race.strip_edges()
	if chosen == "":
		chosen = AccountStore.read_race()
	var payload := new_user_name
	if chosen != "":
		payload += "&" + chosen
	var res: Dictionary = await _call("register", payload, device_hex)
	if bool(res.get("ok", false)):
		var data: Dictionary = res.get("data", {})
		_remember_player(data)
		_mark_authenticated(str(data.get("user_id", "")), str(data.get("user_name", new_user_name)))
		if race != "":
			AccountStore.write_race(race)
	return res


func login(login_user_id: String) -> Dictionary:
	var res: Dictionary = await _call("login", login_user_id, device_hex)
	if bool(res.get("ok", false)):
		var data: Dictionary = res.get("data", {})
		_remember_player(data)
		_mark_authenticated(str(data.get("user_id", login_user_id)), str(data.get("user_name", "")))
	return res


func logout() -> Dictionary:
	var res: Dictionary = await _call("logout")
	if bool(res.get("ok", false)):
		user_id = ""
		user_name = ""
		player_id = ""
		player_name = ""
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


# ---------------------------------------------------------------- 内部

## 登录前带上设备（服务端要靠它认人），登录后就不再重复传 2KB 的 hex。
func _call(action: String, action_data := "", override_device := "") -> Dictionary:
	var device := ""
	if not authenticated:
		device = override_device if override_device != "" else device_hex
	return await _tcp.request(action, action_data, device, "")


func _on_pushed(topic: String, payload: Dictionary) -> void:
	match topic:
		"world":
			world_pushed.emit(payload)
		"event":
			if str(payload.get("kind", "")) == "leave" and str(payload.get("user_id", "")) == user_id:
				set_authenticated(false)
			event_pushed.emit(payload)
		_:
			pass


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
		"INTERNAL_ERROR":
			return "服务器内部错误，请稍后重试"
		"SHUTTING_DOWN":
			return "服务器正在关闭，请稍后重试"
	return message if message != "" else fallback

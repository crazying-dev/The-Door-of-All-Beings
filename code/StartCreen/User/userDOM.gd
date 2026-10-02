extends Control
## 启动页的用户登录控制（Scenes/start/StartCreen.tscn 的 User 节点）。
##
## 设备锁规则：一个设备只能绑定一个账号；账号一旦绑定就不能更换、不能换设备登录。
## 本脚本负责：
##   1. 申请 / 复用设备标识（由服务端签发，落盘在 user://InstallationUniqueID.bin）
##   2. 首次登录：POST /api/v1/register
##   3. 再次登录：POST /api/v1/verify（服务端重新校验 设备 + 用户 ID + 名称 三者一致）
##   4. 用 EndLoadUser(UserIsLoaded) 通知确认面板显示 / 隐藏
##
## 场景里已经连好的信号（StartCreen.tscn L2008-L2012，不可改动）：
##   StartLoadUser()                              根节点在启动动画结束后发出
##   text_submitted(String) / OKButton.pressed    触发 GETUserName
##   LoadUserError(bool)                          预留（本版本不再发出）

const AccountStore = preload("res://code/StartCreen/account_store.gd")

## 账号服务默认地址（服务端部署后生效）。
const API_BASE_DEFAULT := "https://the-door-of-bings.yjlt.top"
## 允许用环境变量 TDOB_API_BASE 覆盖服务地址（本地联调时指向本地服务）。
var API_BASE := _resolve_api_base()
const API_PREFIX := "/api/v1"
## 单次请求超时（秒）。旧版本没设超时，服务端不响应就会永久卡住。
const HTTP_TIMEOUT := 12.0
## 网络类失败的重试次数与间隔（只用于设备标识申请）。
const DEVICE_MAX_ATTEMPT := 3
const RETRY_DELAY := 2.0

@onready var input: LineEdit = $Background/UserNameInput
@onready var OKButton: Button = $Background/OK
@onready var http: HTTPRequest = $UUID4
@onready var waiteLabel: Label = $"../waite"

signal EndLoadUser(UserIsLoaded)

## 本机设备标识（服务端签发）。
var InstallationUniqueID := ""
## 本机是否已有可用账号。
var UserIsLoad := false

## 场景里只有一个 HTTPRequest 节点，必须串行使用。
var _http_busy := false
## 启动阶段确定的状态：unknown / need_name / logged_in / locked / error
var _state := "unknown"
var _message := ""
var _account: Dictionary = {}
var _boot_done := false
var _started := false
var _working := false


func _ready() -> void:
	hide()
	http.timeout = HTTP_TIMEOUT
	if not OKButton.pressed.is_connected(GETUserName):
		OKButton.pressed.connect(GETUserName)
	_bootstrap()


# ================================================================ 启动流程

## 申请设备标识 -> 读本地账号 -> 有账号则校验。全程不改界面可见性，
## 真正的显示 / 隐藏交给 StartLoadUser（启动动画结束后）。
func _bootstrap() -> void:
	if not await _ensure_device():
		_state = "error"
	else:
		_account = AccountStore.read_account()
		if _account.is_empty():
			_state = "need_name"
			_message = "请输入用户名称"
		else:
			await _verify_saved_account()
	_boot_done = true
	if _started:
		_apply_start()


## 申请 / 复用设备标识。成功返回 true，并把服务端回带的账号（本机丢过 load.bin 时）
## 恢复回本地；失败时写入 _message。
func _ensure_device() -> bool:
	var cached := AccountStore.read_device_id()
	var payload := {}
	if cached != "":
		payload["device_id"] = cached
	var res := {}
	for attempt in range(DEVICE_MAX_ATTEMPT):
		res = await _request(HTTPClient.METHOD_POST, "/device", payload)
		if res.get("ok", false):
			break
		if attempt < DEVICE_MAX_ATTEMPT - 1:
			waiteLabel.text = "正在连接账号服务器（%d/%d）..." % [attempt + 1, DEVICE_MAX_ATTEMPT]
			await get_tree().create_timer(RETRY_DELAY).timeout
	if not res.get("ok", false):
		_message = _friendly_error(res, "无法连接账号服务器，请检查网络后重试")
		return false

	var device_id := str(res.get("device_id", "")).strip_edges()
	if device_id == "":
		_message = "服务端未返回设备标识"
		return false
	InstallationUniqueID = device_id
	if device_id != cached:
		AccountStore.write_device_id(device_id)

	# 设备已绑定过账号、而本机账号文件不在（重装或清理过 user://）：直接找回。
	var account: Variant = res.get("account", null)
	if typeof(account) == TYPE_DICTIONARY and AccountStore.read_account().is_empty():
		var user_id := str(account.get("user_id", "")).strip_edges()
		var user_name := str(account.get("user_name", "")).strip_edges()
		if AccountStore.write_account(user_name, user_id):
			print("已从服务端恢复本机账号：%s（%s）" % [user_name, user_id])
	return true


## 本地已有账号：交给服务端重新校验（设备锁的执行点）。
func _verify_saved_account() -> void:
	var res := await _request(HTTPClient.METHOD_POST, "/verify", {
		"device_id": InstallationUniqueID,
		"user_id": str(_account.get("UserID", "")),
		"user_name": str(_account.get("UserName", "")),
	})
	if res.get("ok", false):
		_state = "logged_in"
		_message = "用户已登录"
		return
	match str(res.get("code", "")):
		"USER_NOT_FOUND":
			# 服务端没有这条账号（例如换了服务端）：清掉本地记录，重新取名。
			AccountStore.clear_account()
			_account = {}
			_state = "need_name"
			_message = "原有账号已失效，请重新输入用户名称"
		"DEVICE_MISMATCH":
			_state = "locked"
			_message = "该账号绑定的不是本机设备，无法登录"
		"NAME_MISMATCH":
			_state = "locked"
			_message = "本机账号名称与记录不一致，无法登录"
		"ACCOUNT_LOCKED":
			_state = "locked"
			_message = "账号已被锁定，暂时无法登录"
		_:
			_state = "error"
			_message = _friendly_error(res, "账号校验失败，请稍后重试")


# ================================================================ 对外接口

## 根节点在启动动画结束后发出 StartLoadUser；UserLoaded.LoadUserError 也连到这里。
func StartLoadUser(_UserIsLoad: bool = false) -> void:
	_started = true
	while not _boot_done:
		await get_tree().process_frame
	_apply_start()


## 输入框回车 / 点击“->”按钮触发。
func GETUserName(text: String = "") -> void:
	if _state == "locked":
		waiteLabel.text = _message
		return
	if _state == "error":
		await _restart()
		return
	var user_name := (text if text != "" else input.text).strip_edges()
	if user_name == "":
		waiteLabel.text = "用户名称不能为空"
		input.grab_focus()
		return
	if user_name.length() > AccountStore.USER_NAME_MAX_LEN:
		waiteLabel.text = "用户名称最多 %d 个字符" % AccountStore.USER_NAME_MAX_LEN
		input.grab_focus()
		return
	if _working:
		return
	_working = true
	await _register_flow(user_name, true)
	_working = false


# ================================================================ 界面状态

func _apply_start() -> void:
	waiteLabel.text = _message
	match _state:
		"logged_in":
			UserIsLoad = true
			hide()
			emit_signal("EndLoadUser", true)
		"locked":
			_show_input(false)
			emit_signal("EndLoadUser", false)
		_:
			_show_input(true)
			emit_signal("EndLoadUser", false)


func _show_input(editable: bool) -> void:
	UserIsLoad = false
	input.modulate.a = 1.0
	input.editable = editable
	input.focus_mode = Control.FOCUS_ALL if editable else Control.FOCUS_NONE
	waiteLabel.text = _message
	show()
	if editable:
		input.grab_focus()


# ================================================================ 注册 / 找回

func _register_flow(user_name: String, allow_reissue: bool) -> void:
	waiteLabel.text = "正在注册用户..."
	var res := await _request(HTTPClient.METHOD_POST, "/register", {
		"device_id": InstallationUniqueID,
		"user_name": user_name,
	})
	if res.get("ok", false):
		_finish_login(str(res.get("user_name", user_name)), str(res.get("user_id", "")))
		return
	match str(res.get("code", "")):
		"DEVICE_ALREADY_BOUND":
			if await _adopt_bound_account():
				return
			waiteLabel.text = "本机已绑定其它账号，无法再注册新账号"
		"NAME_TAKEN":
			waiteLabel.text = "该用户名称已被占用，请换一个"
			input.grab_focus()
		"DEVICE_UNKNOWN":
			if allow_reissue and await _reissue_device():
				await _register_flow(user_name, false)
				return
			waiteLabel.text = "设备标识已失效，请重启客户端后重试"
		"ACCOUNT_LOCKED":
			waiteLabel.text = "账号已被锁定，暂时无法注册"
		_:
			waiteLabel.text = _friendly_error(res, "注册失败，请稍后重试")


## 设备已绑定账号：向 /device 取回账号信息并直接登录（找回本地丢失的记录）。
func _adopt_bound_account() -> bool:
	var res := await _request(HTTPClient.METHOD_POST, "/device", {"device_id": InstallationUniqueID})
	if not res.get("ok", false):
		return false
	var account: Variant = res.get("account", null)
	if typeof(account) != TYPE_DICTIONARY:
		return false
	var user_id := str(account.get("user_id", "")).strip_edges()
	var user_name := str(account.get("user_name", "")).strip_edges()
	if not AccountStore.write_account(user_name, user_id):
		return false
	_finish_login(user_name, user_id)
	return true


## 服务端不认识本机设备标识（例如旧版客户端留下的 ID）：重新签发一次。
func _reissue_device() -> bool:
	AccountStore.clear_device_id()
	InstallationUniqueID = ""
	return await _ensure_device()


func _finish_login(user_name: String, user_id: String) -> void:
	user_id = user_id.strip_edges().to_upper()
	if user_id == "" or not AccountStore.write_account(user_name, user_id):
		waiteLabel.text = "服务端未返回有效的用户 ID"
		return
	_account = {"UserName": user_name, "UserID": user_id}
	_state = "logged_in"
	_message = "用户已登录"
	UserIsLoad = true
	waiteLabel.text = "ID已获得"
	hide()
	emit_signal("EndLoadUser", true)


func _restart() -> void:
	if _working:
		return
	_working = true
	_state = "unknown"
	_boot_done = false
	waiteLabel.text = "正在重新连接账号服务器..."
	await _bootstrap()
	_working = false


# ================================================================ HTTP

## 统一请求：返回服务端的 JSON 对象，或 {"ok": false, "code": ..., "msg": ...}。
func _request(method: int, path: String, payload: Dictionary) -> Dictionary:
	while _http_busy:
		await get_tree().process_frame
	_http_busy = true
	var url := API_BASE + API_PREFIX + path
	var headers := PackedStringArray(["Content-Type: application/json"])
	var err := OK
	if method == HTTPClient.METHOD_GET:
		err = http.request(url, headers, HTTPClient.METHOD_GET)
	else:
		err = http.request(url, headers, HTTPClient.METHOD_POST, JSON.stringify(payload))
	var response: Array = []
	if err == OK:
		response = await http.request_completed
	_http_busy = false
	if err != OK:
		return {"ok": false, "code": "REQUEST_FAILED", "msg": "请求发起失败（错误码 %d）" % err}
	return _decode(response)


func _decode(response: Array) -> Dictionary:
	if response.size() < 4:
		return {"ok": false, "code": "NETWORK", "msg": "网络请求未完成"}
	var result: int = response[0]
	var http_code: int = response[1]
	var raw: PackedByteArray = response[3]
	if result != HTTPRequest.RESULT_SUCCESS:
		return {"ok": false, "code": "NETWORK", "msg": "网络请求失败（结果码 %d）" % result}
	var parsed: Variant = JSON.parse_string(raw.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		return {"ok": false, "code": "BAD_RESPONSE", "msg": "服务端返回格式异常（HTTP %d）" % http_code}
	var data: Dictionary = parsed
	if not data.has("ok"):
		data["ok"] = http_code >= 200 and http_code < 300
	if not data.get("ok", false) and not data.has("code"):
		data["code"] = "HTTP_%d" % http_code
	return data


func _friendly_error(res: Dictionary, fallback: String) -> String:
	var msg := str(res.get("msg", "")).strip_edges()
	return msg if msg != "" else fallback


static func _resolve_api_base() -> String:
	var override := OS.get_environment("TDOB_API_BASE").strip_edges()
	return override if override != "" else API_BASE_DEFAULT
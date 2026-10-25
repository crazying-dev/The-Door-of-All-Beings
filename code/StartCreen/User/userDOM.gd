extends Control
## 启动页的用户登录控制（Scenes/start/StartCreen.tscn 的 User 节点）。
##
## 协议 v1.1（规范见 server/protocol.md）：一条 TCP 长连接 + 4 行竖线报文。
## 与旧版 HTTP 时代的差别：
##   · 不再有 /api/v1/register、/api/v1/verify，全部走 socket 动作（device / register / login）
##   · 设备标识由**服务端签发**，本机只负责原样保存（user://InstallationUniqueID.bin）
##   · user_id 由**服务端生成**，注册只提交 user_name
##
## 设备锁规则不变：一个设备只能绑定一个账号；账号一旦绑定就不能更换、不能换设备登录。
##
## 本脚本负责：
##   1. 建连 + hello 握手（失败重试 CONNECT_MAX_ATTEMPT 次）
##   2. 申请 / 复用设备标识（顺带取回该设备已绑定的账号）
##   3. 再次登录：login（服务端重新校验 设备 + 用户 ID 一致）
##   4. 首次登录：register（只提交用户名称，ID 由服务端生成）
##   5. 用 EndLoadUser(UserIsLoaded) 通知确认面板显示 / 隐藏
##   6. 本机已有登录凭证（本地账号文件 / 服务端找回）：跳过 Yes/No 确认，直接进入下一个场景
##      —— 即使凭证已失效、服务端校验不通过，也照样放行；
##      确认面板只服务于「本次启动内新注册」的成功恭喜页。
##
## 场景里已经连好的信号（StartCreen.tscn L2008-L2012，不可改动）：
##   StartLoadUser()                              根节点在启动动画结束后发出
##   text_submitted(String) / OKButton.pressed    触发 GETUserName
##   LoadUserError(bool)                          预留（本版本不再发出）
## 注意：场景里的 UUID4（HTTPRequest）节点已不再使用，本脚本刻意不引用它。

const AccountStore = preload("res://code/StartCreen/account_store.gd")
## 网络业务层：建连 / 握手 / 设备 / 注册 / 登录 都在它里面。
const GameApi = preload("res://code/net/game_api.gd")

## 建连重试次数与间隔（秒）。
const CONNECT_MAX_ATTEMPT := 3
const RETRY_DELAY := 2.0

@onready var input: LineEdit = $Background/UserNameInput
@onready var OKButton: Button = $Background/OK
@onready var waiteLabel: Label = $"../waite"

signal EndLoadUser(UserIsLoaded)

## 本机设备标识（服务端签发，hex 文本，长度由服务端决定）。
var InstallationUniqueID := ""
## 本机是否已有可用账号。
var UserIsLoad := false

## 网络业务层实例，_ready 里用代码挂到本节点下（不改 .tscn）。
## 故意不写类型标注：GameApi 上是自定义方法，一旦静态类型成 Node，
## 调用 connect_and_hello() / explain() 在静态检查阶段就会报“找不到成员”。
var _api = null
## 启动阶段确定的状态：unknown / need_name / logged_in / locked / error
var _state := "unknown"
var _message := ""
var _account: Dictionary = {}
var _boot_done := false
var _started := false
var _working := false
## 本次启动之前本机已有登录凭证（本地账号文件 / 从服务端找回）：不再弹 Yes/No 确认。
var _resumed := false
## 防止重复触发「进入下一个场景」。
var _next_requested := false


func _ready() -> void:
	hide()
	_api = GameApi.new()
	_api.name = "GameApi"
	add_child(_api)
	if not OKButton.pressed.is_connected(GETUserName):
		OKButton.pressed.connect(GETUserName)
	_bootstrap()


# ================================================================ 启动流程

## 建连 -> 申请设备 -> 读本地账号 -> 有账号则登录校验。
## 全程不改界面可见性，真正的显示 / 隐藏交给 StartLoadUser（启动动画结束后）。
func _bootstrap() -> void:
	_state = "unknown"
	_boot_done = false
	var ready := await _connect()
	if ready:
		ready = await _ensure_device()
	if ready:
		# 只要本机已有登录凭证（本地文件，或刚刚由服务端找回）就不再弹确认面板，
		# 与校验结果无关；只有「本次启动内新注册」才需要用户点一次 Yes。
		_resumed = not AccountStore.read_account().is_empty()
		_account = AccountStore.read_account()
		if _account.is_empty():
			_state = "need_name"
			_message = "请输入用户名称"
		else:
			await _verify_saved_account()
	else:
		_state = "error"
		if _message == "":
			_message = "无法连接账号服务器，请检查网络后重试"
	_boot_done = true
	if _started:
		_apply_start()


## 建连 + hello 握手，失败按 RETRY_DELAY 重试。
func _connect() -> bool:
	_message = ""
	for attempt in range(CONNECT_MAX_ATTEMPT):
		waiteLabel.text = "正在连接账号服务器（%d/%d）..." % [attempt + 1, CONNECT_MAX_ATTEMPT]
		if await _api.connect_and_hello():
			return true
		_message = str(_api.last_error)
		if attempt < CONNECT_MAX_ATTEMPT - 1:
			await get_tree().create_timer(RETRY_DELAY).timeout
	return false


## 申请 / 复用设备标识。成功返回 true，并把服务端回带的账号（本机丢过 load.bin 时）
## 恢复回本地；失败时写入 _message。
func _ensure_device() -> bool:
	if not await _api.ensure_device():
		_message = str(_api.last_error)
		return false
	InstallationUniqueID = str(_api.device_hex)
	# 设备已绑定过账号、而本机账号文件不在（重装或清理过 user://）：直接找回。
	var account: Dictionary = _api.bound_account
	if not account.is_empty() and AccountStore.read_account().is_empty():
		var user_id := str(account.get("user_id", "")).strip_edges()
		var user_name := str(account.get("user_name", "")).strip_edges()
		if AccountStore.write_account(user_name, user_id):
			print("已从服务端恢复本机账号：%s（%s）" % [user_name, user_id])
	return true


## 本地已有账号：交给服务端重新登录（设备锁的执行点）。
func _verify_saved_account() -> void:
	var res: Dictionary = await _api.login(str(_account.get("UserID", "")))
	if bool(res.get("ok", false)):
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
		"DEVICE_MISMATCH", "NAME_MISMATCH", "BAD_USER_ID":
			_state = "locked"
			_message = _friendly(res, "该账号绑定的不是本机设备，无法登录")
		"ACCOUNT_LOCKED", "ACCOUNT_DISABLED":
			_state = "locked"
			_message = _friendly(res, "账号已被锁定，暂时无法登录")
		_:
			_state = "error"
			_message = _friendly(res, "账号校验失败，请稍后重试")


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
	if user_name.length() > _name_max_len():
		waiteLabel.text = "用户名称最多 %d 个字符" % _name_max_len()
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
	if _resumed:
		# 本机已有登录凭证：本次启动一律不弹 Yes/No 确认，直接进入下一个场景。
		# 凭证已失效 / 校验不通过 / 网络不通都照样放行（按用户要求）。
		UserIsLoad = true
		hide()
		_goto_next()
		return
	match _state:
		"logged_in":
			# 本次启动内刚注册成功：让确认面板显示一次。
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


## 跳过确认面板，直接进入下一个场景。
## 复用场景里已经连好的 LoadUserOK -> 根节点 gotonexttscn() 通路，
## 避免在脚本里重复硬编码下一场景的路径。
func _goto_next() -> void:
	if _next_requested:
		return
	_next_requested = true
	var panel := get_node_or_null("../UserLoaded")
	if panel != null and panel.has_method("LoadOK"):
		panel.call("LoadOK")
	else:
		get_parent().call("gotonexttscn")


# ================================================================ 注册 / 找回

func _register_flow(user_name: String, allow_reissue: bool) -> void:
	waiteLabel.text = "正在注册用户..."
	# 种族由本机保存的偏好带上去（默认 human）；服务端只在注册时接受一次，
	# 账号已存在的种族不会因登录而被改写。
	var res: Dictionary = await _api.register(user_name, AccountStore.read_race())
	if bool(res.get("ok", false)):
		var data: Dictionary = res.get("data", {})
		_finish_login(str(data.get("user_name", user_name)), str(data.get("user_id", "")))
		return
	match str(res.get("code", "")):
		"DEVICE_ALREADY_BOUND":
			if await _adopt_bound_account():
				return
			waiteLabel.text = "本机已绑定其它账号，无法再注册新账号"
		"NAME_TAKEN":
			waiteLabel.text = "该用户名称已被占用，请换一个"
			input.grab_focus()
		"BAD_DEVICE_ID":
			if allow_reissue and await _reissue_device():
				await _register_flow(user_name, false)
				return
			waiteLabel.text = "设备标识已失效，请重启客户端后重试"
		"ACCOUNT_LOCKED", "ACCOUNT_DISABLED":
			waiteLabel.text = _friendly(res, "账号已被锁定，暂时无法注册")
		_:
			waiteLabel.text = _friendly(res, "注册失败，请稍后重试")


## 设备已绑定账号：向 device 取回账号信息并直接登录（找回本地丢失的记录）。
## 协议里 FAIL 帧装不下 account，所以这里必须再调一次 device（见 protocol.md §9）。
func _adopt_bound_account() -> bool:
	if not await _api.ensure_device():
		return false
	var account: Dictionary = _api.bound_account
	if account.is_empty():
		return false
	var user_id := str(account.get("user_id", "")).strip_edges()
	var user_name := str(account.get("user_name", "")).strip_edges()
	var res: Dictionary = await _api.login(user_id)
	if not bool(res.get("ok", false)):
		waiteLabel.text = _friendly(res, "无法找回本机已绑定的账号")
		return false
	var data: Dictionary = res.get("data", {})
	_finish_login(str(data.get("user_name", user_name)), str(data.get("user_id", user_id)))
	return true


## 服务端不认识本机设备标识（例如旧版客户端留下的 ID / 服务端换了密钥）：重新申领一次。
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


# ================================================================ 工具

## 用户名称长度上限：以服务端 hello 下发的为准，拿不到就用本地常量。
func _name_max_len() -> int:
	var limit := AccountStore.USER_NAME_MAX_LEN
	if _api != null:
		var remote := int(_api.user_name_max_len)
		if remote > 0:
			limit = remote
	return limit


## 服务端错误码 -> 一句人话（具体映射在 game_api.gd）。
func _friendly(res: Dictionary, fallback: String) -> String:
	if _api != null:
		return _api.explain(res, fallback)
	var message := str(res.get("message", "")).strip_edges()
	return message if message != "" else fallback

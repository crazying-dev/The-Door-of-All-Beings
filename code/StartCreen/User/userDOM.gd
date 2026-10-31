extends Control
## 启动页的用户登录控制（Scenes/start/StartCreen.tscn 的 User 节点）。
##
## 协议 v1.3（规范见 server-socket/protocol.md 与 flows.md）：一条 TCP 长连接 + 4 行竖线报文，
## 请求尾是「请求头 + 正文两行」的联合哈希，服务端**不再主动推送**，会话 Token 随 hello 下发。
## 与旧版 HTTP 时代的差别：
##   · 不再有 /api/v1/register、/api/v1/verify，全部走 socket 动作（device / check_name / register / login）
##   · 设备标识由**服务端签发**，本机只负责原样保存（user://InstallationUniqueID.bin + .mac 验签）
##   · user_id 由**服务端生成**，注册提交 `<user_name>&<race>&<player_id>`
##   · player_id 由**本机生成**（RFC 4122 v4 UUID，user://player_id.txt），服务端另存一份
##   · 会话 Token 由 TcpClient 自动维护，本脚本不需要关心
##
## 设备锁规则不变：一个设备只能绑定一个账号；账号一旦绑定就不能更换、不能换设备登录。
##
## 本脚本负责：
##   1. 建连 + hello 握手（失败重试 CONNECT_MAX_ATTEMPT 次）
##   2. 设备标识完整性自检：验签不过就**弹窗问用户**，同意后才删残留文件并重新签发
##   3. 申请 / 复用设备标识（顺带取回该设备已绑定的账号）
##   4. 再次登录：login（服务端重新校验 设备 + 用户 ID 一致）
##   5. 首次登录：check_name 查重 -> register（提交名称 + 种族 + 本机 UUID）
##   6. 用 EndLoadUser(UserIsLoaded) 通知确认面板显示 / 隐藏
##   7. 本机已有登录凭证（本地账号文件 / 服务端找回）：跳过 Yes/No 确认，直接进入下一个场景
##      —— 即使凭证已失效、服务端校验不通过，也照样放行；
##      确认面板只服务于「本次启动内新注册」的成功恭喜页。
##
## 界面提示（v1.3）：场景里那个 waite Label 已被移掉，所以本脚本**用代码**在
## 屏幕底部造一条提示（_hint()）；所有失败原因都同时进日志与这条提示，
## 否则玩家只会看到输入框毫无反应。
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
## 底部提示的停留时长（秒）；换新提示会重置计时。
const HINT_HOLD := 5.0
## 提示字号，以及提示条离屏幕底边的距离（像素）。
const HINT_FONT_SIZE := 26
const HINT_BOTTOM_MARGIN := 48.0

@onready var input: LineEdit = $Background/UserNameInput
@onready var OKButton: Button = $Background/OK

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
## 底部提示 Label（代码创建，不依赖 .tscn；场景里的 waite 组件已被移除）。
var _hint_label: Label = null
## 提示序号：自动隐注时用它判断「这条提示是否已经被后来的提示顶掉」。
var _hint_serial := 0
## 带 CJK 字形的主题（缓存一份，提示 Label 与确认弹窗共用）。
var _ui_theme_cache: Theme = null


func _ready() -> void:
	_build_hint()
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
	# 重置上一轮结论：拒绝重新签发时不能留着上次的 _resumed=true，否则会无名直进下一场景。
	_resumed = false
	var ready := await _connect()
	if ready:
		ready = await _resolve_device_integrity()
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
		print("正在连接账号服务器（%d/%d）..." % [attempt + 1, CONNECT_MAX_ATTEMPT])
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
		_hint(_message, true)
		return
	if _state == "error":
		# 上次设备标识验签没过、用户点了取消：这里再问一次，不要直接重连
		if AccountStore.device_integrity() == AccountStore.INTEGRITY_TAMPERED:
			if not await _confirm_purge_device():
				_hint("已取消：设备标识未通过校验，无法登录", true)
				return
		await _restart()
		return
	var user_name := (text if text != "" else input.text).strip_edges()
	if user_name == "":
		_hint("用户名称不能为空")
		input.grab_focus()
		return
	if user_name.length() > _name_max_len():
		_hint("用户名称最多 %d 个字符" % _name_max_len())
		input.grab_focus()
		return
	if _working:
		return
	_working = true
	await _register_flow(user_name, true)
	_working = false


# ================================================================ 界面状态

func _apply_start() -> void:
	# 本机已有登录凭证：默认不弹 Yes/No 确认、直接进入下一个场景（校验不通过 / 网络不通也照样放行，按用户要求）。
	# 但账号「不存在」被清空、需要重新起名的（need_name）必须停下来弹输入框，
	# 否则提示了「请重新输入用户名称」却又无名自动进世界，前后矛盾。
	if _resumed and _state != "need_name":
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
	# 不可编辑（账号锁定）时提示常驻，可编辑时过几秒自动隐注
	_hint(_message, not editable)
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
	_hint("正在注册用户…")
	# check_name（v1.2 引入、v1.3 沿用）：注册前先查重，避免用户填完才被告知重名。
	# 老服务端没这个动作（回 UNKNOWN_ACTION）时视作「查不了」，直接进注册。
	if not await _name_available(user_name):
		return
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
			_hint("本机已绑定其它账号，无法再注册新账号")
		"NAME_TAKEN":
			_hint("该用户名称已被占用，请换一个")
			input.grab_focus()
		"BAD_DEVICE_ID":
			if allow_reissue and await _reissue_device():
				await _register_flow(user_name, false)
				return
			_hint("设备标识已失效，请重启客户端后重试")
		"ACCOUNT_LOCKED", "ACCOUNT_DISABLED":
			_hint(_friendly(res, "账号已被锁定，暂时无法注册"), true)
		_:
			_hint(_friendly(res, "注册失败，请稍后重试"), true)


## 注册前的名称查重（check_name，v1.2 引入、v1.3 沿用）。
## 返回 true = 继续进注册（含「查不了」的情况）；返回 false = 已经用 _hint() 提示过用户。
## 服务端把「格式非法」也算不可用，用 data.reason 区分（taken / invalid）。
func _name_available(user_name: String) -> bool:
	var res: Dictionary = await _api.check_name(user_name)
	if not bool(res.get("ok", false)):
		# UNKNOWN_ACTION（老服务端）/ 超时 / 断线：一律放过，让 register 自己去判
		return true
	var data: Dictionary = res.get("data", {})
	if bool(data.get("available", true)):
		return true
	match str(data.get("reason", "")):
		"taken":
			_hint("该用户名称已被占用，请换一个")
		"invalid":
			_hint("用户名称最多 %d 个字符，且不能包含空格、| 、& 或控制字符" % _name_max_len())
		_:
			_hint("该用户名称不可用，请换一个")
	input.grab_focus()
	return false


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
		_hint(_friendly(res, "无法找回本机已绑定的账号"), true)
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
		_hint("服务端未返回有效的用户 ID", true)
		return
	_account = {"UserName": user_name, "UserID": user_id}
	_state = "logged_in"
	_message = "用户已登录"
	UserIsLoad = true
	print("ID已获得")
	hide()
	emit_signal("EndLoadUser", true)


func _restart() -> void:
	if _working:
		return
	_working = true
	_state = "unknown"
	_boot_done = false
	_hint("正在重新连接账号服务器…")
	await _bootstrap()
	_working = false


# ================================================================ 界面提示

## 在场景底部造一条提示（场景里的 waite Label 已被移除，只能代码创建）。
## 刻意不改 .tscn：StartCreen.tscn 有 20MB，改它风险远大于收益。
func _build_hint() -> void:
	if _hint_label != null:
		return
	var label := Label.new()
	label.name = "Hint"
	label.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	label.offset_left = 24.0
	label.offset_right = -24.0
	label.offset_top = -HINT_BOTTOM_MARGIN - 44.0
	label.offset_bottom = -HINT_BOTTOM_MARGIN
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	label.theme = _ui_theme()
	label.add_theme_font_size_override("font_size", HINT_FONT_SIZE)
	label.add_theme_color_override("font_color", Color(1.0, 0.94, 0.82))
	label.add_theme_color_override("font_outline_color", Color(0.0, 0.0, 0.0, 0.9))
	label.add_theme_constant_override("outline_size", 6)
	label.visible = false
	add_child(label)
	_hint_label = label


## 中文必须用带 CJK 字形的字体：项目默认主题的字体没有中文，会显示成方块。
## 这里用 SystemFont 取系统字体（与 Scenes/outer/hud.tscn 一致），
## 不依赖 game/assets/（那个目录被 .gitignore 排除，打包时未必带）。
func _ui_theme() -> Theme:
	if _ui_theme_cache != null:
		return _ui_theme_cache
	var font := SystemFont.new()
	font.font_names = PackedStringArray([
		"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
		"Noto Sans CJK SC", "Noto Sans SC", "sans-serif",
	])
	_ui_theme_cache = Theme.new()
	_ui_theme_cache.default_font = font
	_ui_theme_cache.default_font_size = HINT_FONT_SIZE
	return _ui_theme_cache


## 显示一条提示：同时写日志（保留原来的可排查性）并让玩家看得见。
## sticky = true 时一直留着（错误 / 状态说明），否则 HINT_HOLD 秒后自动隐注。
func _hint(text: String, sticky := false) -> void:
	text = text.strip_edges()
	if text != "":
		print(text)
	if _hint_label == null:
		return
	if text == "":
		_hint_label.visible = false
		return
	_hint_label.text = text
	_hint_label.visible = true
	_hint_serial += 1
	if sticky:
		return
	var serial := _hint_serial
	get_tree().create_timer(HINT_HOLD).timeout.connect(func():
		if serial == _hint_serial and is_instance_valid(_hint_label):
			_hint_label.visible = false
	)


## 本地设备标识自检：验签不过时必须**征得用户同意**才删残留文件。
## 返回 false = 用户拒绝，本次登录直接放弃（停在输入页，点“->”可再问一次）。
func _resolve_device_integrity() -> bool:
	if AccountStore.probe_device_integrity() != AccountStore.INTEGRITY_TAMPERED:
		return true
	_hint("设备标识校验未通过：本机文件被改动过，或来自其它电脑", true)
	if not await _confirm_purge_device():
		_state = "error"
		_message = "设备标识校验未通过，已取消重新申请（点“->”可再次确认）"
		return false
	return true


## 弹窗问「是否删除残留的设备标识与本地账号，并向服务器重新申请」。
## 同意 = 真的删（AccountStore.purge_identity）；取消 / 关窗 = 什么都不动。
func _confirm_purge_device() -> bool:
	var dialog := ConfirmationDialog.new()
	dialog.title = "设备标识需要重新申请"
	dialog.dialog_text = "本机的设备标识没有通过完整性校验：文件被改动过，或者这份存档来自其它电脑。\n\n" \
		+ "继续将删除本机的设备标识与账号记录，并向服务器重新申请一个新的设备标识；" \
		+ "之后需要重新输入用户名称（旧设备上的绑定关系不会自动恢复）。\n\n" \
		+ "是否继续？"
	dialog.ok_button_text = "删除并重新申请"
	dialog.cancel_button_text = "取消"
	dialog.theme = _ui_theme()
	dialog.min_size = Vector2i(620, 280)
	add_child(dialog)
	var result := {"done": false, "ok": false}
	dialog.confirmed.connect(func():
		result["done"] = true
		result["ok"] = true
	)
	dialog.canceled.connect(func():
		result["done"] = true
		result["ok"] = false
	)
	# × 按钮 / 关窗不一定发 canceled，补一条兜底
	dialog.close_requested.connect(func():
		if not bool(result["done"]):
			result["done"] = true
			result["ok"] = false
	)
	dialog.popup_centered()
	while not bool(result["done"]):
		await get_tree().process_frame
	if is_instance_valid(dialog):
		dialog.queue_free()
	if bool(result["ok"]):
		AccountStore.purge_identity()
		InstallationUniqueID = ""
		_hint("已删除残留的设备标识，正在重新申请…")
		return true
	return false


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

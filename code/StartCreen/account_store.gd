extends RefCounted
## 账号 / 设备标识的本地存储层：只负责读写 user:// 下的两个文件，不做任何网络操作。
##
##   user://InstallationUniqueID.bin     设备标识（服务端签发的不透明票据文本）
##   user://InstallationUniqueID.bin.mac 设备标识的完整性校验码（v1.3 防篡改，见下）
##   user://load.bin                  账号信息（JSON：UserName / UserID）
##   user://race.txt                  本机选定的种族 key（纯文本，默认 human）
##   user://player_id.txt             本机玩家 UUID（v1.2 引入、v1.3 沿用：客户端自己生成，注册时交给服务端）
##
## 单独抽一层的原因：登录流程（userDOM.gd）与确认面板（UserLoaded.gd）都要读它，
## 而且旧版本写坏过的内容（例如把 UserID 写成了 null）需要在这里统一兜住。
##
## 设备标识防篡改（协议 v1.3 / Req6）：设备 ID 文件旁配一个 .mac 校验文件，
## 内容是 HMAC-SHA256（密钥 = 机器指纹 + 内置盐）对 ID 原文的签名。
## 读取时先验签：文件被手改 / 从别的机器拷来（指纹不同）都会校验失败，绝不带着脏值去请求。
## **验不过时不自动删**：probe_device_integrity() 只把结论记为 "tampered"，由登录页弹窗
## 征得用户同意后再 purge_identity() 清掉（见 userDOM._resolve_device_integrity）。
## 原因：改计算机名 / 换 Windows 用户 / 换机器都会让指纹变，静默删除等于让用户凭空丢号。

const DEVICE_PATH := "user://InstallationUniqueID.bin"
## 设备标识的完整性校验码文件（v1.3 防篡改）：与 DEVICE_PATH 同基名 + .mac 后缀。
const DEVICE_MAC_PATH := DEVICE_PATH + ".mac"
const ACCOUNT_PATH := "user://load.bin"
## 种族选择单独存一个小文件：账号文件只认 UserName / UserID，
## 混在一起的话任何一次 write_account() 都会把种族洗掉。
const RACE_PATH := "user://race.txt"
## player_id（v1.2 引入、v1.3 沿用）：客户端生成的 RFC 4122 v4 UUID，注册时随 payload 交给服务端。
## 与服务端 user_id 是两个东西 —— user_id 仍由服务端生成并作权威主键。
const PLAYER_ID_PATH := "user://player_id.txt"

## 服务端 `game.DEFAULT_RACE` 同值：没选过种族时用它。
const DEFAULT_RACE := "human"
## 种族 key 限字母 / 数字 / 下划线，与服务端种族表对齐（human / elf / …）。
const RACE_PATTERN := "^[a-z0-9_]{1,16}$"

## 服务端签发的用户 ID 形如 ZWME-2WKQ（4 位 + "-" + 4 位）。
const USER_ID_PATTERN := "^[A-Z0-9]{4}-[A-Z0-9]{4}$"
## 本机 player_id 的形状：小写 RFC 4122 v4 UUID（8-4-4-4-12）。
## 服务端只把它当不透明字符串存，格式对不上也只是本地自检失败，不影响联机。
const PLAYER_ID_PATTERN := "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
## 与服务端 Config.USER_NAME_MAX_LEN 保持一致。
const USER_NAME_MAX_LEN := 24


# ---------------------------------------------------------------- 基础读写

static func read_text(path: String) -> String:
	if not FileAccess.file_exists(path):
		return ""
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		push_warning("读取失败：%s（错误码 %d）" % [path, FileAccess.get_open_error()])
		return ""
	var text := file.get_as_text()
	file.close()
	return text.strip_edges()


static func write_text(path: String, text: String) -> bool:
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		push_error("写入失败：%s（错误码 %d）" % [path, FileAccess.get_open_error()])
		return false
	file.store_string(text)
	file.close()
	return true


static func remove_file(path: String) -> void:
	if not FileAccess.file_exists(path):
		return
	var err := DirAccess.remove_absolute(path)
	if err != OK:
		push_warning("删除失败：%s（错误码 %d）" % [path, err])


# ---------------------------------------------------------------- 设备标识

## 内置盐：只用于搅动本地校验密钥，不算机密（本地防篡改的强度上限就是
## 「拷到别的机器 / 手改文件」这一级）。真正的权威校验在服务端（HMAC 票据）。
const _DEVICE_MAC_PEPPER := "TDOB-device-v1"

## 本机校验密钥 = 机器指纹 + 盐 的 SHA256。Godot 4 没有跨平台的 OS.get_unique_id()，
## 改用稳定的机器属性组合（计算机名 / 用户名 / 系统名 / CPU 核数）：
## 同一份文件拷到别的机器上指纹不同，MAC 对不上，直接判为篡改。
## 只用本函数里已在用的 OS 方法（get_environment / get_name / get_processor_count）；
## Godot 4.7 的 OS 没有 get_architecture()，Engine.get_singleton().call() 也只是绕过编译、
## 方法不存在照样会在运行时报 Nonexistent function，故直接去掉架构这一项。
static func _machine_fingerprint() -> String:
	var env := OS.get_environment("COMPUTERNAME")
	if env == "":
		env = OS.get_environment("HOSTNAME")
	return "%s|%s|%s|%d" % [
		env,
		OS.get_environment("USERNAME"),
		OS.get_name(),
		OS.get_processor_count(),
	]


static func _device_mac_key() -> PackedByteArray:
	return _sha256_hex(_DEVICE_MAC_PEPPER + "|" + _machine_fingerprint()).hex_decode()


## 本文件自包含的 SHA256（hex），不依赖 code/net/tcp_client.gd。
static func _sha256_hex(text: String) -> String:
	var ctx := HashingContext.new()
	ctx.start(HashingContext.HASH_SHA256)
	ctx.update(text.to_utf8_buffer())
	return ctx.finish().hex_encode()


## 对设备 ID 原文算 HMAC-SHA256，返回小写 hex。
static func _device_mac(device_id: String) -> String:
	var ctx := HMACContext.new()
	var err := ctx.start(HashingContext.HASH_SHA256, _device_mac_key())
	if err != OK:
		push_error("HMAC 初始化失败（错误码 %d），设备标识将按不可用处理" % err)
		return ""
	ctx.update(device_id.to_utf8_buffer())
	return ctx.finish().hex_encode()


## 恒定时间字符串比较：不用引擎类（ConstantTime 在部分 4.x 版本不存在），
## 自己逐字节累积差异，中途不短路，避免时序侧信道。
static func _const_equal(a: String, b: String) -> bool:
	if a.length() != b.length():
		return false
	var diff := 0
	for i in a.length():
		diff |= a.substr(i, 1).to_utf8_buffer()[0] ^ b.substr(i, 1).to_utf8_buffer()[0]
	return diff == 0


## 本地自检的三种结论（probe_device_integrity / device_integrity 的返回值）。
const INTEGRITY_OK := ""
## 文件不存在：正常情况（首次运行 / 主动清过）。
const INTEGRITY_MISSING := "missing"
## 有明确问题：验签不过 / 只剩半个文件。**不自动删**，等 UI 征得用户同意再清。
const INTEGRITY_TAMPERED := "tampered"

## 最近一次自检的结论（static：本层是 RefCounted 静态类，没有实例状态）。
static var _integrity := INTEGRITY_OK


## 最近一次自检的结论："" / "missing" / "tampered"。
static func device_integrity() -> String:
	return _integrity


## 清掉自检结论（用户已确认清除文件，或重新签发成功后用）。
static func clear_integrity_state() -> void:
	_integrity = INTEGRITY_OK


## 只读自检：**不删除任何文件**，结论写进 device_integrity()。
## "tampered" 必须由 UI 弹窗征得同意后才能清除（见 userDOM._resolve_device_integrity）。
static func probe_device_integrity() -> String:
	var device_id := read_text(DEVICE_PATH)
	if device_id == "":
		# 只剩校验码没有 ID：算残留，同样视为异常
		_integrity = INTEGRITY_TAMPERED if FileAccess.file_exists(DEVICE_MAC_PATH) else INTEGRITY_MISSING
		return _integrity
	var stored_mac := read_text(DEVICE_MAC_PATH).to_lower()
	if stored_mac == "":
		# 旧版本写的裸文件（没有校验码）：就地补签一次，避免升级把老用户洗成重领。
		# 注意：这一步等于信任旧内容，只发生在新旧版本交接的第一次读取。
		if not write_device_id(device_id):
			_integrity = INTEGRITY_TAMPERED
			return _integrity
		return INTEGRITY_OK
	var expect := _device_mac(device_id)
	if expect == "" or not _const_equal(expect, stored_mac):
		_integrity = INTEGRITY_TAMPERED
		return _integrity
	_integrity = INTEGRITY_OK
	return _integrity


## 读取设备标识：先验完整性校验码，验不过 = 被篡改 / 拷自别的机器。
## 返回空串表示「不可用」（不存在 / 校验失败），调用方按无设备去服务端重领；
## 但**不清文件**——清除必须显式走 purge_identity()，由 UI 确认后调用。
static func read_device_id() -> String:
	if probe_device_integrity() == INTEGRITY_TAMPERED:
		push_warning("设备标识完整性校验失败（疑似被修改或来自其它电脑）")
		return ""
	return read_text(DEVICE_PATH)


## 写入设备标识：ID 与校验码两份文件必须都成功才算落盘，写坏就全清掉。
static func write_device_id(device_id: String) -> bool:
	device_id = device_id.strip_edges()
	if device_id == "":
		return false
	var mac := _device_mac(device_id)
	if mac == "":
		return false
	if not write_text(DEVICE_PATH, device_id):
		return false
	if not write_text(DEVICE_MAC_PATH, mac):
		remove_file(DEVICE_PATH)
		return false
	clear_integrity_state()
	return true


static func clear_device_id() -> void:
	remove_file(DEVICE_PATH)
	remove_file(DEVICE_MAC_PATH)
	clear_integrity_state()


## 「删除残留文件，重新签发」（必须由 UI 征得用户同意后调用）：
## 设备标识与本地账号是绑定关系，只清 ID 会卡在 DEVICE_MISMATCH（登录）/
## DEVICE_ALREADY_BOUND（注册），所以两样一起清，让用户走一次全新注册。
static func purge_identity() -> void:
	clear_device_id()
	clear_account()


# ---------------------------------------------------------------- 玩家标识

## 读取本机 player_id。文件不存在 / 内容不是合法 UUID 一律返回空串。
static func read_player_id() -> String:
	var player_id := read_text(PLAYER_ID_PATH).to_lower()
	if not is_valid_player_id(player_id):
		return ""
	return player_id


static func write_player_id(player_id: String) -> bool:
	player_id = player_id.strip_edges().to_lower()
	if not is_valid_player_id(player_id):
		push_warning("拒绝写入非法 player_id：%s" % player_id)
		return false
	return write_text(PLAYER_ID_PATH, player_id)


static func clear_player_id() -> void:
	remove_file(PLAYER_ID_PATH)


## 读本机 player_id，没有 / 写坏了就当场生成一张并落盘。生成失败返回空串。
static func ensure_player_id() -> String:
	var player_id := read_player_id()
	if player_id != "":
		return player_id
	player_id = generate_player_id()
	if not write_player_id(player_id):
		return ""
	return player_id


## 生成一个 RFC 4122 v4 UUID（小写 hex，8-4-4-4-12）。
## 版本位（第 7 字节高 4 位 = 4）与变体位（第 9 字节高 2 位 = 10）按规范打上，
## 这样 is_valid_player_id() 才认。
static func generate_player_id() -> String:
	var bytes := Crypto.new().generate_random_bytes(16)
	if bytes.size() != 16:
		# 极端平台 Crypto 不可用时的兜底：用时间做种凑 16 字节，够用即可
		bytes = _fallback_uuid_bytes()
	bytes[6] = (bytes[6] & 0x0F) | 0x40
	bytes[8] = (bytes[8] & 0x3F) | 0x80
	var hex := bytes.hex_encode()
	return "%s-%s-%s-%s-%s" % [
		hex.substr(0, 8), hex.substr(8, 4), hex.substr(12, 4),
		hex.substr(16, 4), hex.substr(20, 12),
	]


static func _fallback_uuid_bytes() -> PackedByteArray:
	var out := PackedByteArray()
	out.resize(16)
	var state := Time.get_ticks_usec() & 0x7FFFFFFF
	for i in range(16):
		state = (state * 1103515245 + 12345) & 0x7FFFFFFF
		out[i] = (state >> 8) & 0xFF
	return out


static func is_valid_player_id(player_id: String) -> bool:
	if player_id == "":
		return false
	var re := RegEx.new()
	if re.compile(PLAYER_ID_PATTERN) != OK:
		return false
	return re.search(player_id) != null


# ---------------------------------------------------------------- 账号

## 读取本地账号。文件不存在 / 不是 JSON 对象 / 关键字段缺失 / 旧版写坏的 null
## 统一按"没有账号"处理，返回空字典。
static func read_account() -> Dictionary:
	var text := read_text(ACCOUNT_PATH)
	if text == "":
		return {}
	var parsed: Variant = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		return {}
	var user_id := str(parsed.get("UserID", "")).strip_edges().to_upper()
	var user_name := str(parsed.get("UserName", "")).strip_edges()
	if user_name == "" or not is_valid_user_id(user_id):
		return {}
	return {"UserName": user_name, "UserID": user_id}


static func write_account(user_name: String, user_id: String) -> bool:
	user_name = user_name.strip_edges()
	user_id = user_id.strip_edges().to_upper()
	if user_name == "" or not is_valid_user_id(user_id):
		push_error("拒绝写入非法账号：user_name=%s user_id=%s" % [user_name, user_id])
		return false
	var payload := {"UserName": user_name, "UserID": user_id}
	return write_text(ACCOUNT_PATH, JSON.stringify(payload, "\t"))


static func clear_account() -> void:
	remove_file(ACCOUNT_PATH)


static func is_valid_user_id(user_id: String) -> bool:
	if user_id == "":
		return false
	var re := RegEx.new()
	if re.compile(USER_ID_PATTERN) != OK:
		return false
	return re.search(user_id) != null


# ---------------------------------------------------------------- 种族

## 读取本机选定的种族 key。未选过 / 内容不合法一律返回 DEFAULT_RACE。
static func read_race() -> String:
	var race := read_text(RACE_PATH).to_lower()
	if race == "" or not is_valid_race(race):
		return DEFAULT_RACE
	return race


static func write_race(race: String) -> bool:
	race = race.strip_edges().to_lower()
	if not is_valid_race(race):
		push_warning("拒绝写入非法种族 key：%s" % race)
		return false
	return write_text(RACE_PATH, race)


static func clear_race() -> void:
	remove_file(RACE_PATH)


static func is_valid_race(race: String) -> bool:
	if race == "":
		return false
	var re := RegEx.new()
	if re.compile(RACE_PATTERN) != OK:
		return false
	return re.search(race) != null
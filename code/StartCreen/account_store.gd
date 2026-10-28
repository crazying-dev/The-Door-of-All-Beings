extends RefCounted
## 账号 / 设备标识的本地存储层：只负责读写 user:// 下的两个文件，不做任何网络操作。
##
##   user://InstallationUniqueID.bin  设备标识（服务端签发并落库的 UUID 文本）
##   user://load.bin                  账号信息（JSON：UserName / UserID）
##   user://race.txt                  本机选定的种族 key（纯文本，默认 human）
##   user://player_id.txt             本机玩家 UUID（协议 v1.2：客户端自己生成，注册时交给服务端）
##
## 单独抽一层的原因：登录流程（userDOM.gd）与确认面板（UserLoaded.gd）都要读它，
## 而且旧版本写坏过的内容（例如把 UserID 写成了 null）需要在这里统一兜住。

const DEVICE_PATH := "user://InstallationUniqueID.bin"
const ACCOUNT_PATH := "user://load.bin"
## 种族选择单独存一个小文件：账号文件只认 UserName / UserID，
## 混在一起的话任何一次 write_account() 都会把种族洗掉。
const RACE_PATH := "user://race.txt"
## 协议 v1.2 的 player_id：客户端生成的 RFC 4122 v4 UUID，注册时随 payload 交给服务端。
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

static func read_device_id() -> String:
	return read_text(DEVICE_PATH)


static func write_device_id(device_id: String) -> bool:
	device_id = device_id.strip_edges()
	if device_id == "":
		return false
	return write_text(DEVICE_PATH, device_id)


static func clear_device_id() -> void:
	remove_file(DEVICE_PATH)


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
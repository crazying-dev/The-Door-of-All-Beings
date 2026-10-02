extends RefCounted
## 账号 / 设备标识的本地存储层：只负责读写 user:// 下的两个文件，不做任何网络操作。
##
##   user://InstallationUniqueID.bin  设备标识（服务端签发并落库的 UUID 文本）
##   user://load.bin                  账号信息（JSON：UserName / UserID）
##
## 单独抽一层的原因：登录流程（userDOM.gd）与确认面板（UserLoaded.gd）都要读它，
## 而且旧版本写坏过的内容（例如把 UserID 写成了 null）需要在这里统一兜住。

const DEVICE_PATH := "user://InstallationUniqueID.bin"
const ACCOUNT_PATH := "user://load.bin"

## 服务端签发的用户 ID 形如 ZWME-2WKQ（4 位 + "-" + 4 位）。
const USER_ID_PATTERN := "^[A-Z0-9]{4}-[A-Z0-9]{4}$"
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
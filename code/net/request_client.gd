extends RefCounted
## 统一的信息发送模块：客户端所有对外 HTTP 请求都从这里发出。
##
## 每一次请求都保证带上软件版本（版本号唯一来源是 AppInfo）：
##   · 请求头 X-Client-Version / X-Client-Platform / X-Client-Locale
##   · POST 的 JSON 体里同时写一份 client_version（兼容只读 body 的服务端）
##
## 场景里只有一个 HTTPRequest 节点，所以这里用 _busy 串行化，
## 避免两个请求同时打到同一个节点上互相覆盖。

const AppInfo = preload("res://code/net/app_info.gd")

## 单次请求超时（秒）。
const DEFAULT_TIMEOUT := 12.0

var _http: HTTPRequest
var _busy := false


func _init(http: HTTPRequest, timeout: float = DEFAULT_TIMEOUT) -> void:
	_http = http
	if is_ready() and timeout > 0.0:
		_http.timeout = timeout


func is_ready() -> bool:
	return _http != null and is_instance_valid(_http)


## 当前客户端版本，供 UI 显示或日志使用。
func version() -> String:
	return AppInfo.version()


## 发一次请求并返回服务端 JSON 对象。
## 网络层失败时统一归一成 {"ok": false, "code": ..., "msg": ...}，调用方无需再包一层。
func send(method: int, url: String, payload: Dictionary = {}) -> Dictionary:
	if not is_ready():
		return {"ok": false, "code": "NO_HTTP", "msg": "网络组件未就绪"}

	while _busy:
		await _http.get_tree().process_frame
	_busy = true

	var headers := AppInfo.build_headers()
	var body := payload.duplicate(true)
	body[AppInfo.FIELD_VERSION] = AppInfo.version()

	var err := OK
	if method == HTTPClient.METHOD_GET:
		err = _http.request(url, headers, HTTPClient.METHOD_GET)
	else:
		err = _http.request(url, headers, method, JSON.stringify(body))

	var response: Array = []
	if err == OK:
		response = await _http.request_completed
	_busy = false

	if err != OK:
		return {"ok": false, "code": "REQUEST_FAILED", "msg": "请求发起失败（错误码 %d）" % err}
	return _decode(response)


## 把 HTTPRequest 的四元组返回值解成服务端的 JSON 对象。
static func _decode(response: Array) -> Dictionary:
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

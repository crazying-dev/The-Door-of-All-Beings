extends RefCounted
## 服务端地址的唯一来源。
##
## 默认连生产服务器；本地联调时用环境变量覆盖，不用改代码：
##
##   set TDOB_SERVER_HOST=127.0.0.1
##   set TDOB_SERVER_PORT=5423
##
## HTTP 接口只用于「发现」（可选），游戏数据全走 TCP。

## 生产默认地址。
const HOST_DEFAULT := "the-door-of-bings.yjlt.top"
const PORT_DEFAULT := 5423
const API_BASE_DEFAULT := "https://the-door-of-bings.yjlt.top"

const ENV_HOST := "TDOB_SERVER_HOST"
const ENV_PORT := "TDOB_SERVER_PORT"
const ENV_API_BASE := "TDOB_API_BASE"

## 发现接口路径（服务端 http_api.py）。
const ENDPOINT_PATH := "/api/v1/endpoint"


static func host() -> String:
	var value := OS.get_environment(ENV_HOST).strip_edges()
	return value if value != "" else HOST_DEFAULT


static func port() -> int:
	var value := OS.get_environment(ENV_PORT).strip_edges()
	if value == "":
		return PORT_DEFAULT
	var number := value.to_int()
	if number <= 0 or number > 65535:
		push_warning("%s=%s 不是合法端口，回退到 %d" % [ENV_PORT, value, PORT_DEFAULT])
		return PORT_DEFAULT
	return number


static func api_base() -> String:
	var value := OS.get_environment(ENV_API_BASE).strip_edges()
	return value if value != "" else API_BASE_DEFAULT


static func endpoint_url() -> String:
	return api_base() + ENDPOINT_PATH


static func describe() -> String:
	return "%s:%d" % [host(), port()]

extends RefCounted
## 服务端地址的唯一来源。
##
## 【两台机器分工】（务必分清，2026-10 拆服）：
##   * API_BASE（机器A）：HTTP 发现服务，**只有域名** the-door-of-bings.yjlt.top，
##     443 走 nginx；客户端先 GET /api/v1/endpoint 向它问到 TCP 地址。
##   * HOST/PORT（机器B）：TCP 游戏服，**只有公网 IP** 38.76.190.249:5423（无域名），
##     域名不监听 5423。下面两个常量是「发现失败时的直连回退」，不是首选。
##
## 正常流程下 game_api 会用发现接口返回的地址覆盖这里。
## 本地联调可用环境变量覆盖（直连模式时生效）：
##
##   set TDOB_SERVER_HOST=127.0.0.1
##   set TDOB_SERVER_PORT=5423

## 生产默认地址：
## HOST/PORT = 发现失败时的 TCP 直连回退（机器B 的公网 IP）。
const HOST_DEFAULT := "38.76.190.249"
const PORT_DEFAULT := 5423
## API_BASE = HTTP 发现服务（机器A）的域名。
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

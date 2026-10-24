extends RefCounted
## 客户端软件信息的唯一来源。
##
## 版本号以 project.godot 的 application/config/version 为准（导出时随包一致），
## 这里只做兜底，避免某天配置项被删掉后请求头里出现空版本。
## 所有对外请求都必须通过本模块取版本，不要在业务脚本里再硬编码。

## project.godot 里登记的版本键。
const VERSION_SETTING := "application/config/version"
## 配置缺失时的兜底版本。
const FALLBACK_VERSION := "0.1.0"

## 请求头字段名（服务端可任选其一读取）。
const HEADER_VERSION := "X-Client-Version"
const HEADER_PLATFORM := "X-Client-Platform"
const HEADER_LOCALE := "X-Client-Locale"
## POST 请求体里同样写一份版本，兼容只读 body 的服务端实现。
const FIELD_VERSION := "client_version"


## 当前客户端版本，例如 "0.1.0"。
static func version() -> String:
	var value := str(ProjectSettings.get_setting(VERSION_SETTING, "")).strip_edges()
	if value == "":
		value = FALLBACK_VERSION
	return value


## 运行平台名，例如 "Windows"。
static func platform() -> String:
	return OS.get_name()


## 系统语言，例如 "zh_CN"。
static func locale() -> String:
	return OS.get_locale()


## 构造统一请求头：Content-Type + 版本 + 平台 + 语言。
static func build_headers(with_content_type: bool = true) -> PackedStringArray:
	var headers := PackedStringArray()
	if with_content_type:
		headers.append("Content-Type: application/json")
	headers.append("%s: %s" % [HEADER_VERSION, version()])
	headers.append("%s: %s" % [HEADER_PLATFORM, platform()])
	headers.append("%s: %s" % [HEADER_LOCALE, locale()])
	return headers

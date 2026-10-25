extends Node
## 众生之门 TCP 协议客户端（协议 v1.1，规范见 server/protocol.md）。
##
## 职责边界：只负责「把一条请求发出去、把对应的响应拿回来」，不懂业务。
## 业务封装在 game_api.gd；地址在 net_config.gd。
##
## 设计要点：
##   * **长连接**：一个 TcpClient 只用一条 TCP，登录后同一连接收 PUSH。
##   * **串行化**：同一时刻只允许一条请求在路上（服务端回帧不带请求 id，
##     并发就会错配），后来的 call 会排队。
##   * **不阻塞主线程**：全部用 await process_frame 轮询，不开线程。
##   * **每次请求都带客户端版本**（协议头第 5 字段），符合项目约定。

const AppInfo = preload("res://code/net/app_info.gd")

## 协议常量（必须与服务端 protocol.py 一致）。
const MAGIC := "TheDoorOfBings"
const PROTOCOL_VERSION := "1.1"
const ENCRYPTION := "none"

## 单行上限，超过就丢弃缓冲区（服务端默认 16384）。
const MAX_LINE_BYTES := 16384
## 单次读的块大小。
const READ_CHUNK := 8192
## 往返均值的平滑系数（越大越跟手，越小越平滑）。
const RTT_SMOOTH := 0.25

## 一条非 PUSH 帧。
signal response_received(resp: Dictionary)
## 服务端主动推送：topic 为 "world" / "event"。
signal pushed(topic: String, payload: Dictionary)
## 连接状态变化。
signal connection_changed(connected: bool)

## 整条请求的超时（含建连与等待响应）。
var timeout_sec := 12.0

## 往返耗时统计。
## 注意：等响应是按帧轮询的，所以读数自带 1 帧（约 16ms @60fps）的量化误差。
## 最近一次请求的往返耗时（毫秒），-1 表示还没有样本。
var last_rtt_ms := -1.0
## 往返耗时的指数平滑均值（毫秒），-1 表示还没有样本。
var avg_rtt_ms := -1.0
## 已采集的往返样本数。
var rtt_samples := 0

var _peer := StreamPeerTCP.new()
var _rx := PackedByteArray()
var _inbox: Array = []
## 请求串行化锁（GDScript 里用 bool + await 当互斥锁用）。
var _busy := false
var _connecting := false
var _was_connected := false

var _client_version := AppInfo.FALLBACK_VERSION
var _platform := ""
var _locale := ""


func _ready() -> void:
	_client_version = AppInfo.version()
	_platform = AppInfo.platform()
	_locale = AppInfo.locale()
	set_process(true)


# ---------------------------------------------------------------- 连接

func is_connected_to_server() -> bool:
	return _peer.get_status() == StreamPeerTCP.STATUS_CONNECTED


## 建立 TCP 连接（await）。已连上直接返回 true。
func connect_to(host: String, port: int) -> bool:
	if is_connected_to_server():
		return true
	while _connecting:
		await get_tree().process_frame
		if is_connected_to_server():
			return true

	_connecting = true
	_peer = StreamPeerTCP.new()
	var err := _peer.connect_to_host(host, port)
	if err != OK:
		_connecting = false
		push_warning("connect_to_host(%s:%d) 失败：%d" % [host, port, err])
		return false

	var deadline := Time.get_ticks_msec() + int(timeout_sec * 1000.0)
	while Time.get_ticks_msec() < deadline:
		_peer.poll()
		var status := _peer.get_status()
		if status == StreamPeerTCP.STATUS_CONNECTED:
			_peer.set_no_delay(true)
			_rx = PackedByteArray()
			_inbox.clear()
			reset_rtt()
			_connecting = false
			_set_connected(true)
			return true
		if status == StreamPeerTCP.STATUS_ERROR or status == StreamPeerTCP.STATUS_NONE:
			break
		await get_tree().process_frame

	_connecting = false
	_peer.disconnect_from_host()
	_set_connected(false)
	return false


func disconnect_from_server() -> void:
	_peer.disconnect_from_host()
	_rx = PackedByteArray()
	_inbox.clear()
	reset_rtt()
	_set_connected(false)


## 清空往返统计（新建 / 断开连接时用）。
func reset_rtt() -> void:
	last_rtt_ms = -1.0
	avg_rtt_ms = -1.0
	rtt_samples = 0


## 记录一次往返耗时并更新平滑均值。
func _record_rtt(sent_usec: int) -> void:
	var rtt := float(Time.get_ticks_usec() - sent_usec) / 1000.0
	last_rtt_ms = rtt
	rtt_samples += 1
	avg_rtt_ms = rtt if avg_rtt_ms < 0.0 else lerpf(avg_rtt_ms, rtt, RTT_SMOOTH)


func _set_connected(value: bool) -> void:
	if _was_connected == value:
		return
	_was_connected = value
	connection_changed.emit(value)


# ---------------------------------------------------------------- 收包

func _process(_delta: float) -> void:
	var status := _peer.get_status()
	if status != StreamPeerTCP.STATUS_CONNECTED:
		if _was_connected:
			reset_rtt()
			_set_connected(false)
		return

	_peer.poll()
	while true:
		var available := _peer.get_available_bytes()
		if available <= 0:
			break
		var chunk: Array = _peer.get_data(mini(available, READ_CHUNK))
		if int(chunk[0]) != OK:
			break
		var data: PackedByteArray = chunk[1]
		if data.is_empty():
			break
		_rx.append_array(data)

	_drain_lines()


## 把缓冲区里的完整行全部取出来分发。
func _drain_lines() -> void:
	while true:
		var index := _rx.find(10)   # '\n'
		if index < 0:
			if _rx.size() > MAX_LINE_BYTES:
				push_error("收到超长帧（%d 字节），已丢弃缓冲区" % _rx.size())
				_rx = PackedByteArray()
			return
		var line := _rx.slice(0, index).get_string_from_utf8().strip_edges()
		_rx = _rx.slice(index + 1)
		if line != "":
			_handle_line(line)


## 解析一条服务端帧：OK | action | json / FAIL | action | code | message / PUSH | topic | json
func _handle_line(line: String) -> void:
	var first := line.split("|", true, 1)
	var kind := first[0].strip_edges()

	if kind == "PUSH":
		var push_parts := line.split("|", true, 2)
		var topic := push_parts[1].strip_edges() if push_parts.size() > 1 else ""
		var payload := _parse_json(push_parts[2] if push_parts.size() > 2 else "{}")
		pushed.emit(topic, payload)
		return

	if kind == "FAIL":
		# message 里可能有 |，所以最多切 3 刀，末段整体归 message
		var fail_parts := line.split("|", true, 3)
		var fail_resp := {
			"kind": "FAIL",
			"ok": false,
			"action": fail_parts[1].strip_edges() if fail_parts.size() > 1 else "",
			"code": fail_parts[2].strip_edges() if fail_parts.size() > 2 else "UNKNOWN",
			"message": fail_parts[3].strip_edges() if fail_parts.size() > 3 else "",
			"data": {},
		}
		_inbox.append(fail_resp)
		response_received.emit(fail_resp)
		return

	# OK（以及不认识的帧）一律当 OK 处理，没响应体时给空字典
	var ok_parts := line.split("|", true, 2)
	var ok_resp := {
		"kind": kind if kind != "" else "UNKNOWN",
		"ok": kind == "OK",
		"action": ok_parts[1].strip_edges() if ok_parts.size() > 1 else "",
		"code": "" if kind == "OK" else "UNKNOWN_FRAME",
		"message": "" if kind == "OK" else line,
		"data": _parse_json(ok_parts[2] if ok_parts.size() > 2 else "{}"),
	}
	_inbox.append(ok_resp)
	response_received.emit(ok_resp)


static func _parse_json(text: String) -> Dictionary:
	var parsed: Variant = JSON.parse_string(text.strip_edges())
	return parsed if typeof(parsed) == TYPE_DICTIONARY else {}


# ---------------------------------------------------------------- 发请求

## 发送一条请求并等待对应响应（await）。返回统一结构的 Dictionary。
##
## 返回：{kind, ok, action, code, message, data}
## 网络层错误用伪 code：DISCONNECTED / NETWORK_ERROR / TIMEOUT。
func request(action: String, action_data := "", device_hex := "", user_id := "") -> Dictionary:
	while _busy:
		await get_tree().process_frame
	_busy = true
	var resp := await _request_once(action, action_data, device_hex, user_id)
	_busy = false
	return resp


func _request_once(action: String, action_data: String, device_hex: String, user_id: String) -> Dictionary:
	if not is_connected_to_server():
		return _fake_fail("DISCONNECTED", "未连接到服务器")

	_inbox.clear()
	var sent_usec := Time.get_ticks_usec()
	var packet := build_packet(action, action_data, device_hex, user_id)
	var err := _peer.put_data(packet)
	if err != OK:
		return _fake_fail("NETWORK_ERROR", "发送失败（错误码 %d）" % err)

	var deadline := Time.get_ticks_msec() + int(timeout_sec * 1000.0)
	while Time.get_ticks_msec() < deadline:
		if not _inbox.is_empty():
			var resp: Dictionary = _inbox.pop_front()
			# 已经超时丢掉的老响应不归本次请求，继续等
			var resp_action := str(resp.get("action", ""))
			if resp_action == action or resp_action == "-":
				# 只有真拿到服务端的回复才算一次往返（本地伪失败不计）。
				if str(resp.get("kind", "")) != "NETWORK_ERROR":
					_record_rtt(sent_usec)
				return resp
			continue
		if not is_connected_to_server():
			return _fake_fail("DISCONNECTED", "连接已断开")
		await get_tree().process_frame

	return _fake_fail("TIMEOUT", "服务器响应超时")


static func _fake_fail(code: String, message: String) -> Dictionary:
	return {"kind": "FAIL", "ok": false, "action": "", "code": code, "message": message, "data": {}}


# ---------------------------------------------------------------- 组包

## 按协议 v1.1 拼一条 4 行报文。
## 哈希必须对「第 2 行原文 + \n + 第 3 行原文」逐字节计算，不要 strip。
func build_packet(action: String, action_data: String, device_hex: String, user_id: String) -> PackedByteArray:
	var ts := int(Time.get_unix_time_from_system())
	var head := "%s | %s | %d | %s | %s | %s | %s" % [
		MAGIC, PROTOCOL_VERSION, ts, ENCRYPTION,
		_client_version, _platform, _locale,
	]
	var body1 := "%s | %s" % [action, action_data]
	var body2 := "%s | %s" % [device_hex, user_id]
	var digest := sha256_hex(body1 + "\n" + body2)
	return (head + "\n" + body1 + "\n" + body2 + "\n" + digest + " | " + digest + "\n").to_utf8_buffer()


static func sha256_hex(text: String) -> String:
	var ctx := HashingContext.new()
	ctx.start(HashingContext.HASH_SHA256)
	ctx.update(text.to_utf8_buffer())
	return ctx.finish().hex_encode()

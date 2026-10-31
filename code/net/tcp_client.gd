extends Node
## 众生之门 TCP 协议客户端（协议 v1.3，规范见 server-socket/protocol.md 与 flows.md）。
##
## 职责边界：只负责「把一条请求发出去、把对应的响应拿回来」，不懂业务。
## 业务封装在 game_api.gd；地址在 net_config.gd。
##
## 设计要点：
##   * **长连接**：一个 TcpClient 只用一条 TCP。
##   * **拉取式（v1.3）**：服务端不再主动推任何帧，世界/在线数据由上层按自选频率拉取。
##   * **串行化**：同一时刻只允许一条请求在路上（服务端回帧不带请求 id，
##     并发就会错配），后来的 call 会排队。批量请求走 request_batch，一次收多帧。
##   * **不阻塞主线程**：全部用 await process_frame 轮询，不开线程。
##   * **每次请求都带客户端版本**（协议头第 5 字段），符合项目约定。
##   * **联合哈希（v1.3）**：请求尾哈希覆盖「请求头 + 正文两行」，改头部即失效。
##   * **会话 Token（v1.3）**：不再建连推送，改为从首个 hello 响应里取（adopt_token），
##     本类把它放进后续每条请求的第 3 行。没 token 时退回 v1.1 的 2 段写法。

const AppInfo = preload("res://code/net/app_info.gd")

## 协议常量（必须与服务端 protocol.py 一致）。
const MAGIC := "TheDoorOfBings"
const PROTOCOL_VERSION := "1.3"
const ENCRYPTION := "none"

## 推送 topic（v1.3 服务端不再下发，保留仅为兼容）。
const TOPIC_TOKEN := "token"
const TOPIC_RESET_TOKEN := "reset_token"

## 单行上限，超过就丢弃缓冲区（服务端默认 16384）。
const MAX_LINE_BYTES := 16384
## 单次读的块大小。
const READ_CHUNK := 8192
## 往返均值的平滑系数（越大越跟手，越小越平滑）。
const RTT_SMOOTH := 0.25

## 报文级（整批）失败码：这些错误都发生在 dispatch **之前**，服务端只会回一帧，
## 批量请求收到它们就该立刻停止等其它帧，否则要干等到超时。
## （实测：带错 token 发 2 个动作，服务端只回 `FAIL | state | BAD_SESSION_TOKEN | …` 一帧。）
const BATCH_FATAL_CODES := [
	"BAD_MAGIC", "BAD_VERSION", "BAD_ENCRYPTION", "BAD_PLAIN_HASH", "BAD_CIPHER_HASH",
	"MALFORMED", "TOO_LARGE", "REQUEST_EXPIRED",
	"BAD_SESSION_TOKEN", "SESSION_TOKEN_EXPIRED",
	"NOT_READY", "SHUTTING_DOWN", "TOO_MANY_CONNECTIONS",
]

## 一条非 PUSH 帧。
signal response_received(resp: Dictionary)
## 服务端主动推送：topic 为 "world" / "event" / "token" / "reset_token"。
signal pushed(topic: String, payload: Dictionary)
## 连接状态变化。
signal connection_changed(connected: bool)
## 服务端下发 / 换发了会话 Token（v1.2）。空串表示已清除。
signal token_changed(token: String)
## 服务端要求重置 Token（拿的是旧连接 / 已作废的值，见 flows.md 流程 5）。
signal token_reset(reason: String)

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

## 本连接的会话 Token（v1.2）。空串 = 还没有 / 已作废，此时按 v1.1 的 2 段报文发。
var session_token := ""
## Token 的参考有效期（秒，服务端下发的 expire；仅用于日志 / 展示）。
var token_expire := 0

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
			session_token = ""
			token_expire = 0
			reset_rtt()
			_connecting = false
			# v1.3：建连后服务端不发任何帧；Token 随首个 hello 响应下发（见 game_api）
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
	clear_token()
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


# ---------------------------------------------------------------- 会话 Token

## 有没有可用的会话 Token。
func has_token() -> bool:
	return session_token != ""


## 服务端回包里回显的 token（login 响应会带一份），外部可直接覆盖。
func adopt_token(value: String) -> void:
	_apply_token(value)


## 清掉本地 token（拿到 reset_token / 重连前）。
func clear_token() -> void:
	if session_token == "" and token_expire == 0:
		return
	session_token = ""
	token_expire = 0
	token_changed.emit("")


func _apply_token(value: String) -> void:
	value = value.strip_edges()
	if value == "" or value == session_token:
		return
	session_token = value
	token_changed.emit(value)


## 从推送帧里吸收 token / reset_token（v1.3 服务端已不再推；保留仅为兼容老服务端）。
func _absorb_push_token(topic: String, payload: Dictionary) -> void:
	if topic == TOPIC_TOKEN:
		token_expire = int(payload.get("expire", 0))
		_apply_token(str(payload.get("token", "")))
	elif topic == TOPIC_RESET_TOKEN:
		# 先清掉再告诉监听者：它直接重登就能拿回新值
		clear_token()
		token_reset.emit(str(payload.get("reason", "")))


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
		_absorb_push_token(topic, payload)
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


## 发送一条批量请求（v1.3）：一条报文携带多个动作，服务端按序返回多帧。
## calls 形如 [["state","1&2&3&4"], ["peers",""]]。返回与 calls 等长的响应数组：
## 服务端只回了一帧（报文级错误）时，末尾会补成**同一条错误码**（不是全部 TIMEOUT）。
func request_batch(calls: Array, device_hex := "", user_id := "") -> Array:
	while _busy:
		await get_tree().process_frame
	_busy = true
	var res := await _request_batch_once(calls, device_hex, user_id)
	_busy = false
	return res


func _request_batch_once(calls: Array, device_hex: String, user_id: String) -> Array:
	var n := calls.size()
	if not is_connected_to_server():
		return _fill_fails(n, "DISCONNECTED", "未连接到服务器")

	_inbox.clear()
	var sent_usec := Time.get_ticks_usec()
	var packet := build_packet("", "", device_hex, user_id, calls)
	var err := _peer.put_data(packet)
	if err != OK:
		return _fill_fails(n, "NETWORK_ERROR", "发送失败（错误码 %d）" % err)

	var deadline := Time.get_ticks_msec() + int(timeout_sec * 1000.0)
	var out: Array = []
	var aborted := false
	while out.size() < n and not aborted and Time.get_ticks_msec() < deadline:
		while not _inbox.is_empty() and out.size() < n:
			var frame: Dictionary = _inbox.pop_front()
			out.append(frame)
			# 报文级失败：服务端只回这一帧（action 还可能是第一个动作名），别死等其它帧
			if not bool(frame.get("ok", false)) and BATCH_FATAL_CODES.has(str(frame.get("code", "")).strip_edges()):
				aborted = true
				break
		if out.size() >= n or aborted:
			break
		if not is_connected_to_server():
			break
		await get_tree().process_frame
	if out.size() == n:
		_record_rtt(sent_usec)
		return out
	# 报文级错误（token 失效 / 哈希不符 / 版本不认）会让服务端只回一帧：
	# 把缺的补成**同一个错误码**，上层才能按真实原因处理（否则会误判成超时）。
	var code := "TIMEOUT"
	var message := "批量响应不足（服务端可能只回了一帧）"
	if not out.is_empty():
		var first: Dictionary = out[0]
		if not bool(first.get("ok", false)) and str(first.get("code", "")) != "":
			code = str(first.get("code", ""))
			message = str(first.get("message", ""))
			if message == "":
				message = "整批请求被服务端拒绝"
	while out.size() < n:
		out.append(_fake_fail(code, message))
	return out


static func _fill_fails(n: int, code: String, message: String) -> Array:
	var arr: Array = []
	for _i in n:
		arr.append(_fake_fail(code, message))
	return arr


# ---------------------------------------------------------------- 组包

## 按协议 v1.3 拼一条 4 行报文。
## 联合哈希对「第 1 行原文 + \n + 第 2 行原文 + \n + 第 3 行原文」逐字节计算，不要 strip。
## 第 3 行带 token 时是 3 段（`<token> | <device> | <user>`），
## 没 token 时退回 v1.1 的 2 段（`<device> | <user>`），服务端两种都认。
## 传了 calls（[[a,d],…]）时第 2 行为批量格式 `a1|d1;;a2|d2`。
func build_packet(action: String, action_data: String, device_hex: String, user_id: String, calls: Array = []) -> PackedByteArray:
	var ts := int(Time.get_unix_time_from_system())
	var head := "%s | %s | %d | %s | %s | %s | %s" % [
		MAGIC, PROTOCOL_VERSION, ts, ENCRYPTION,
		_client_version, _platform, _locale,
	]
	var body1 := _join_batch(calls) if not calls.is_empty() else "%s | %s" % [action, action_data]
	var body2 := "%s | %s" % [device_hex, user_id]
	if session_token != "":
		body2 = "%s | %s | %s" % [session_token, device_hex, user_id]
	# v1.3 联合哈希：覆盖「请求头 + 正文两行」
	var digest := sha256_hex(head + "\n" + body1 + "\n" + body2)
	return (head + "\n" + body1 + "\n" + body2 + "\n" + digest + " | " + digest + "\n").to_utf8_buffer()


## 把 [[a1,d1],[a2,d2],…] 拼成第 2 行的批量正文 `a1 | d1;;a2 | d2`。
static func _join_batch(calls: Array) -> String:
	var parts: PackedStringArray = []
	for c in calls:
		parts.append("%s | %s" % [c[0], c[1]])
	return ";;".join(parts)


static func sha256_hex(text: String) -> String:
	var ctx := HashingContext.new()
	ctx.start(HashingContext.HASH_SHA256)
	ctx.update(text.to_utf8_buffer())
	return ctx.finish().hex_encode()

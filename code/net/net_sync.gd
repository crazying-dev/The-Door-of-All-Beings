extends Node
## 世界场景的网络同步层（联机）。
##
## 职责：
##   1. 进入世界后用本地账号静默重登（code/net/game_api.gd::login_with_saved_account）；
##   2. 以 10Hz 上报本机玩家的坐标 / 朝向 / 动作；
##   3. 消费服务端 10Hz 的 PUSH world，把其它玩家渲染成 player.tscn 的线条骨架代理，
##      位置做插值，动作按 state 字段切 walk / idle；
##   4. 收到 PUSH event（join / leave）时增删代理。
##
## 设计要点：
##   * 全部用代码 add_child 挂进场景，world.tscn 不需要（也不能轻易）改动；
##   * 断线 / 未登录时静默降级，单机玩完全不受影响；
##   * 远端代理是 player.tscn 的实例，preview_keys 关掉，免得按 1/2/3 也切它们的动画。

const GameApi = preload("res://code/net/game_api.gd")
const PlayerScene = preload("res://Scenes/actors/player.tscn")

## 上报间隔（秒）。与服务端 PUSH_HZ 对齐。
const STATE_INTERVAL := 0.1
## 远端代理位置插值速度（越大越“硬”）。
const LERP_SPEED := 8.0
## 速度高于这个值就当走路（与服务端 state 字段的两档对齐）。
const WALK_SPEED := 0.5
## 移动中给服务端报的动作名。服务端只认 idle / moving / dead，
## 早期客户端报的 "walk" 会被归成 idle，远端就看不出你在走。
const MOTION_MOVING := "moving"
const MOTION_IDLE := "idle"
## 三围初值（客户端目前还没有成长 / 伤害系统，先用满值上报）。
const DEFAULT_STAT := 100.0

## 世界物品快照（首次拉取，以及 world_rev 变化时随 PUSH 下发）。
signal world_items_received(items: Array)
## 世界元信息（服务端 `world` 响应新增的 `world` 字段：id / name / seed / rev）。
signal world_meta_received(meta: Dictionary)

## 业务层实例。故意不写类型标注：GameApi 上是自定义方法，静态类型成 Node 会报找不到成员。
var _api = null
## 本机玩家（player_fp.gd，提供 get_yaw_degrees / get_horizontal_speed）。
var _player: Node3D = null
## 远端代理的挂载点。
var _root: Node3D = null
var _avatars: Dictionary = {}
var _targets: Dictionary = {}
var _accum := 0.0
var _busy := false
## 当前在线人数（含自己）。
var online_count := 0
## 本机上报的三围（当前值），后期由战斗 / 成长系统驱动。
var hp := DEFAULT_STAT
var stamina := DEFAULT_STAT
var mana := DEFAULT_STAT
## 世界元信息（拉 world 时缓下）。
var world_meta: Dictionary = {}


func _ready() -> void:
	_api = GameApi.new()
	_api.name = "GameApi"
	add_child(_api)
	_api.world_pushed.connect(_on_world)
	_api.event_pushed.connect(_on_event)
	_api.authenticated_changed.connect(_on_authenticated)
	set_process(false)


## 由 world.gd 调用：注入本机玩家与远端代理挂载点，然后开始同步。
func setup(player: Node3D, root_node: Node3D) -> void:
	_player = player
	_root = root_node if root_node != null else get_parent() as Node3D
	_bootstrap()


func _bootstrap() -> void:
	set_process(false)
	var ok: bool = await _api.login_with_saved_account()
	if not ok:
		push_warning("联机同步未登录：%s" % str(_api.last_error))
		return
	print("联机同步已就绪：%s @ 服务端 %s（协议 %s，推送 %.0fHz）" % [
		_api.user_name, _api.server_version, _api.protocol_version, _api.push_hz,
	])
	# 首次进来把世界物品拉一次（后续只在 world_rev 变化时才随 PUSH 下发）。
	var res: Dictionary = await _api.fetch_world()
	if bool(res.get("ok", false)):
		var data: Dictionary = res.get("data", {})
		world_meta = _api.world_meta
		if not world_meta.is_empty():
			world_meta_received.emit(world_meta)
		world_items_received.emit(data.get("items", []))
	set_process(true)


# ---------------------------------------------------------------- 主循环

func _process(delta: float) -> void:
	_tick_avatars(delta)
	if _busy or _player == null or _api == null:
		return
	if not bool(_api.authenticated):
		return
	_accum += delta
	if _accum < STATE_INTERVAL:
		return
	_accum = 0.0
	_busy = true
	await _push_state()
	_busy = false


func _push_state() -> void:
	var pos: Vector3 = _player.global_position
	var yaw := 0.0
	var speed := 0.0
	if _player.has_method("get_yaw_degrees"):
		yaw = float(_player.call("get_yaw_degrees"))
	if _player.has_method("get_horizontal_speed"):
		speed = float(_player.call("get_horizontal_speed"))
	var motion := MOTION_MOVING if speed > WALK_SPEED else MOTION_IDLE
	var res: Dictionary = await _api.send_state(pos, yaw, hp, motion, stamina, mana)
	if bool(res.get("ok", false)):
		return
	var code := str(res.get("code", ""))
	if code == "NOT_AUTHENTICATED" or code == "DISCONNECTED" or code == "BAD_USER_ID":
		push_warning("联机上报被拒：%s" % str(_api.explain(res, code)))
		_api.set_authenticated(false)


# ---------------------------------------------------------------- 远端代理

func _on_world(payload: Dictionary) -> void:
	var players: Array = payload.get("players", [])
	online_count = int(payload.get("online", players.size()))
	if payload.has("items"):
		world_items_received.emit(payload.get("items", []))
	var seen := {}
	for entry in players:
		if typeof(entry) != TYPE_DICTIONARY:
			continue
		var row: Dictionary = entry
		var uid := str(row.get("user_id", "")).strip_edges()
		if uid == "" or uid == str(_api.user_id):
			continue
		seen[uid] = true
		_targets[uid] = {
			"pos": Vector3(
				float(row.get("pos_x", 0.0)),
				float(row.get("pos_y", 0.0)),
				float(row.get("pos_z", 0.0)),
			),
			"yaw": float(row.get("yaw", 0.0)),
		}
		var avatar = _avatars.get(uid, null)
		if avatar == null:
			avatar = _spawn_avatar(uid)
		if avatar != null:
			_set_motion(avatar, str(row.get("state", "idle")))
	# 这一轮没出现的就地拆除（已下线）
	for uid in _avatars.keys():
		if not seen.has(uid):
			_despawn_avatar(uid)


func _spawn_avatar(uid: String) -> Node3D:
	if _root == null:
		return null
	var node: Node = PlayerScene.instantiate()
	var avatar := node as Node3D
	if avatar == null:
		node.queue_free()
		return null
	avatar.name = "Remote_" + uid.replace("-", "_")
	# 预览键只留给本机 / 编辑器，远端代理不该响应 1 / 2 / 3。
	avatar.set("preview_keys", false)
	avatar.position = Vector3.ZERO
	_root.add_child(avatar)
	_avatars[uid] = avatar
	return avatar


func _despawn_avatar(uid: String) -> void:
	var avatar = _avatars.get(uid, null)
	_avatars.erase(uid)
	_targets.erase(uid)
	if avatar != null and is_instance_valid(avatar):
		avatar.queue_free()


func _clear_avatars() -> void:
	for uid in _avatars.keys():
		_despawn_avatar(uid)
	_targets.clear()
	online_count = 0


func _set_motion(avatar: Node3D, state: String) -> void:
	if not avatar.has_method("play_motion"):
		return
	# 服务端只发 idle / moving / dead；walk / run 是早期客户端自己报的旧词，一并兼容。
	var motion := "walk" if state == "moving" or state == "walk" or state == "run" else "idle"
	if avatar.has_method("current_motion") and str(avatar.call("current_motion")) == motion:
		return
	avatar.call("play_motion", motion)


func _tick_avatars(delta: float) -> void:
	if _targets.is_empty():
		return
	var t := clampf(delta * LERP_SPEED, 0.0, 1.0)
	for uid in _avatars.keys():
		var avatar = _avatars.get(uid, null)
		if avatar == null or not is_instance_valid(avatar):
			_avatars.erase(uid)
			_targets.erase(uid)
			continue
		var target: Dictionary = _targets.get(uid, {})
		if target.is_empty():
			continue
		var current: Vector3 = avatar.global_position
		avatar.global_position = current.lerp(target["pos"], t)
		avatar.rotation.y = lerp_angle(avatar.rotation.y, deg_to_rad(float(target["yaw"])), t)


func _on_event(payload: Dictionary) -> void:
	match str(payload.get("kind", "")):
		"leave":
			_despawn_avatar(str(payload.get("user_id", "")))
		_:
			pass


func _on_authenticated(value: bool) -> void:
	if not value:
		_clear_avatars()


# ---------------------------------------------------------------- 对外查询

func is_online() -> bool:
	return _api != null and bool(_api.authenticated)


func local_user_id() -> String:
	return "" if _api == null else str(_api.user_id)


## 网络往返耗时（毫秒，平滑值）；未登录 / 无样本返回 -1。
func latency_ms() -> float:
	if _api == null or not bool(_api.authenticated):
		return -1.0
	return float(_api.latency_ms())


## 当前世界名（来自服务端 `world` 元信息），没拿到返回空串。
func world_name() -> String:
	return str(world_meta.get("name", ""))


## 最近一次上报回写的行修订号（排错用：能看出自己的数据有没有被服务端收下）。
func state_rev() -> int:
	return 0 if _api == null else int(_api.state_rev)


## 改本机三围（当前值）。后期由战斗 / 成长系统调用。
func set_stats(new_hp: float, new_stamina: float, new_mana: float) -> void:
	hp = clampf(new_hp, 0.0, DEFAULT_STAT)
	stamina = clampf(new_stamina, 0.0, DEFAULT_STAT)
	mana = clampf(new_mana, 0.0, DEFAULT_STAT)

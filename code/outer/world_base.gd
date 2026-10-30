extends Node3D
## 门外大厅 / 门内世界两个场景共享的编排基类。
##
## 拆场景后，world.gd 里那些「与在哪个世界无关」的逻辑都归拢到这里：
## 输入兜底、触屏控制层、鼠标捕获、掉落保护、复位、调试面板刷新、联机层启动。
## 子类（lobby.gd / inner_main.gd）只负责各自的节点引用与入口逻辑。
##
## 约定：两个场景里玩家节点都叫 $Player、HUD 都叫 $HUD（hud.tscn 的实例）、
## 本界落点节点都叫 $Spawn，基类据此取用，子类 _ready 里赋值。

## 方案B 触屏控制层脚本（运行时构建，不进 .tscn；键鼠平台不实例化）。
const TouchControlsScript := preload("res://code/outer/touch_controls.gd")

## 传送后的冷却时间（秒）：防止落地瞬间又被感应区触发一次。
const TELEPORT_COOLDOWN := 1.2
## 掉到这个高度以下就送回本界落点。
const FALL_LIMIT := -40.0

const HINT_TEXT := "WASD 移动   ·   Shift 疾行   ·   空格 跳跃   ·   走近光柱进入门内世界（单向）   ·   Esc 释放鼠标   ·   R 复位   ·   F3 调试面板（帧率 / 延迟 / 坐标）   ·   H 关闭本说明"
## 触屏平台的提示文案（与键鼠不同源，没有 Esc / F3 / H 这些键盘操作）。
const HINT_TEXT_TOUCH := "左侧拖动移动（推到底疾行）　·　右侧拖动转向　·　右下跳跃　·　走近光柱入门（单向）"

## 万一 project.godot 里的输入动作丢了，运行时补一份，保证一定操作得起来。
const FALLBACK_ACTIONS := {
	"move_forward": [KEY_W, KEY_UP],
	"move_back": [KEY_S, KEY_DOWN],
	"move_left": [KEY_A, KEY_LEFT],
	"move_right": [KEY_D, KEY_RIGHT],
	"jump": [KEY_SPACE],
	"sprint": [KEY_SHIFT],
	"reset_player": [KEY_R],
	"toggle_debug": [KEY_F3],
	"toggle_help": [KEY_H],
}

## 故意不写类型标注：player_fp.gd / hud.gd 上是自定义方法，
## 一旦静态类型成 Node3D / CanvasLayer，调用 teleport_to_point() / set_hint() 就会报“找不到成员”。
var _player = null
var _hud = null
## 本界落点（掉落保护 / 按 R 复位时送回去），子类在 _ready 里指认。
var _spawn_point = null
## 方案B 触屏控制层（仅触屏设备创建，否则 null）。故意不写类型标注。
var _touch = null
## 联机同步层（code/net/net_sync.gd）。用代码挂上来而不是改场景文件；
## 只有门内世界会调用 _setup_net_sync()，门外保持 null（纯离线等待界面）。
## 故意不写类型标注：net_sync.gd 上是自定义脚本，静态类型成 Node 会报找不到成员。
var _net_sync = null

var _cooldown: float = 0.0
var _speed_accum: float = 0.0


func _process(delta: float) -> void:
	_cooldown = maxf(_cooldown - delta, 0.0)

	if _player != null and _player.global_position.y < FALL_LIMIT:
		_respawn()

	# 调试面板的读数每 0.1 秒刷一次就够了，别每帧都拼字符串。
	# 面板隐藏时干脆不算，省掉无意义的字符串拼接（帧率取的是 Engine 的统计值，代价极低）。
	_speed_accum += delta
	if _speed_accum >= 0.1 and _hud != null and _player != null:
		_speed_accum = 0.0
		if _hud.is_debug_visible():
			_hud.set_fps(Engine.get_frames_per_second())
			_hud.set_latency(_latency_ms())
			_hud.set_speed(_player.get_horizontal_speed())
			_hud.set_coords(_player.global_position)
			_hud.set_facing(_player.get_yaw_degrees())


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
		get_viewport().set_input_as_handled()
		return

	if event is InputEventMouseButton:
		# 触屏平台不做鼠标捕获（触摸会被模拟成鼠标点击，误触发捕获）。
		if _touch == null:
			var click := event as InputEventMouseButton
			if click.pressed and Input.mouse_mode != Input.MOUSE_MODE_CAPTURED:
				Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
				get_viewport().set_input_as_handled()
		return

	if event.is_action_pressed("reset_player"):
		_respawn()
		get_viewport().set_input_as_handled()


## 当前网络延迟（毫秒，平滑值）。未联机 / 联机层缺失时返回 -1，HUD 会显示“未连接”。
func _latency_ms() -> float:
	if _net_sync == null or not _net_sync.has_method("latency_ms"):
		return -1.0
	return float(_net_sync.call("latency_ms"))


## 送回本界落点（掉出去或按 R 时用）。子类可通过覆写 _respawn 改成“按坐标落点”。
func _respawn() -> void:
	if _player == null:
		return
	if _spawn_point != null:
		_player.teleport_to(_spawn_point)
	_cooldown = TELEPORT_COOLDOWN
	if _hud != null:
		_hud.flash_white()


## 联机同步层只启动一次（门内世界进入时调用）；门外等待期永不调用，_net_sync 保持 null。
func _setup_net_sync() -> void:
	if _net_sync != null:
		return
	var script: Variant = load("res://code/net/net_sync.gd")
	if script == null:
		push_warning("未找到 net_sync.gd，联机同步已关闭")
		return
	_net_sync = script.new()
	_net_sync.name = "NetSync"
	add_child(_net_sync)
	_net_sync.setup(_player, self)


## 联机同步层（可能为 null）。net_sync.gd 内部会回调它取当前世界 / 玩家等信息。
func net_sync():
	return _net_sync


## 触屏 / 移动平台：运行时构建方案B 控制层并交给 player 轮询；键鼠平台直接返回（_touch 保持 null）。
## 用 HUD（CanvasLayer）当父节点：控件直接叠在游戏画面之上，且不影响世界坐标。
func _setup_touch_controls() -> void:
	var use_touch := OS.has_feature("mobile") or DisplayServer.is_touchscreen_available()
	if not use_touch:
		_hud.set_hint(HINT_TEXT)
		return
	_touch = TouchControlsScript.new()
	_touch.name = "TouchControls"
	_hud.add_child(_touch)
	if _player != null:
		_player.touch_controls = _touch
	_hud.set_hint(HINT_TEXT_TOUCH)


## 键鼠平台：捕获鼠标做视角；触屏平台不捕获，视角交给拖拽手势。
func _setup_mouse() -> void:
	if _touch == null:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _register_fallback_actions() -> void:
	for action_name in FALLBACK_ACTIONS.keys():
		if InputMap.has_action(action_name):
			continue
		InputMap.add_action(action_name)
		for keycode in FALLBACK_ACTIONS[action_name]:
			var key_event := InputEventKey.new()
			key_event.physical_keycode = keycode
			InputMap.action_add_event(action_name, key_event)

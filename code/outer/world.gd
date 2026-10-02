extends Node3D
## 场景总管：单向穿界（门外纯黑虚空 -> 门内世界）、环境与光照切换、掉落保护、输入兜底。
##
## 两个世界仍然在同一张场景里，只是沿 Z 轴错开 WORLD_OFFSET_Z 米。
## 门外什么都没有：一片看不见尽头的地板 + 一根由发光粒子堆出来的光柱。
## 走近光柱 -> 白光由远及近铺满屏幕 -> 被随机丢进门内世界的灵质台上（全程不锁操作）。
## 单向：门内世界不再放门，进去就回不来。
##
## 门内世界目前是「只摆一块地板」的简化版：地形与装饰留到之后再补，
## 所以这里只保证「落得下去、站得住、能跑」，其余逻辑与最终版一致。

## 两个世界的名字（下标就是世界索引：0 = 门外，1 = 门内）。
const WORLD_NAMES := ["门外的虚空", "众生之门 · 门内世界"]
## 两个世界的间距（门内世界那个 Node3D 就摆在 z = 6000）。
## 门外地板是 4000x4000（z 只到 ±2000），间距取 6000 是留足余量：
## 站到门外地板的边缘，也既看不见、也碰不到 6000 米外的门内世界。
const WORLD_OFFSET_Z := 6000.0
## 传送后的冷却时间（秒）：防止落地瞬间又被感应区触发一次。
const TELEPORT_COOLDOWN := 1.2
## 掉到这个高度以下就送回本界落点（门外地板够大，这层兜底主要是防门内世界掉出地板）。
const FALL_LIMIT := -40.0
## 白闪推进 / 淡出的时长（秒）。
const WIPE_IN_TIME := 1.15
const WIPE_OUT_TIME := 0.75
## 门内随机落点的最大半径。半径 30 是「之后那块灵质台」的半径，这里留 8 米余量；
## 目前的简化地板远大于 30，所以一定站得住。
const ARRIVAL_RADIUS := 22.0
const HINT_TEXT := "WASD 移动   ·   Shift 疾行   ·   空格 跳跃   ·   走近光柱进入门内世界（单向）   ·   Esc 释放鼠标   ·   R 复位"

## 万一 project.godot 里的输入动作丢了，运行时补一份，保证一定操作得起来。
const FALLBACK_ACTIONS := {
	"move_forward": [KEY_W, KEY_UP],
	"move_back": [KEY_S, KEY_DOWN],
	"move_left": [KEY_A, KEY_LEFT],
	"move_right": [KEY_D, KEY_RIGHT],
	"jump": [KEY_SPACE],
	"sprint": [KEY_SHIFT],
	"reset_player": [KEY_R],
}

@export_group("环境")
## 门外（虚空）：灰渐变天空 + 环境光关闭。在 world.tscn 里用 SubResource 指过来。
@export var outer_environment: Environment
## 门内（众生之门世界）：云海辉光 + 暖色。
@export var inner_environment: Environment

@onready var _world_environment: WorldEnvironment = $WorldEnvironment
@onready var _inner_world: Node3D = $Worlds/InnerWorld
@onready var _inner_sun: DirectionalLight3D = $Worlds/InnerWorld/SunLight
@onready var _outer_spawn: Node3D = $Worlds/OuterWorld/Spawn
@onready var _inner_spawn: Node3D = $Worlds/InnerWorld/Spawn

## 光柱的静态类型故意写成 Node：light_pillar.gd 自定义了 gate_entered 信号，
## 一旦写成 Node3D，connect("gate_entered", ...) 在静态检查阶段就会报“找不到信号”。
@onready var _pillar: Node = $Worlds/OuterWorld/Pillar

## 故意不写类型标注：player_fp.gd / hud.gd 上是自定义方法，
## 一旦静态类型成 Node3D / CanvasLayer，调用 teleport_to_point() / set_wipe_progress() 就会报“找不到成员”。
var _player
var _hud

var _current_world: int = 0
var _cooldown: float = 0.0
var _speed_accum: float = 0.0
## 白闪过场期间为 true：屏蔽感应区的重复触发（操作不锁，白幕只是一层动画）。
var _transitioning: bool = false


func _ready() -> void:
	_register_fallback_actions()

	_player = $Player
	_hud = $HUD

	# 用字符串形式连接信号：光柱脚本自定义了 gate_entered，静态类型 Node 上找不到它。
	_pillar.connect("gate_entered", _on_gate_entered)

	# 开场只切环境、不挪人：玩家就摆在 world.tscn 里那个正对光柱的起始点。
	_apply_world(0)
	_cooldown = 0.0
	_hud.set_world_name(WORLD_NAMES[0])
	_hud.set_hint(HINT_TEXT)
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


## 只切环境 / 只点亮门内世界的太阳。
## 门外那套 Environment 是灰色的天空 + 全黑的地面，门外压根没有太阳 —— 唯一的可见光源就是那根光柱。
func _apply_world(world: int) -> void:
	_current_world = world
	if outer_environment != null and inner_environment != null:
		_world_environment.environment = outer_environment if world == 0 else inner_environment
	_inner_sun.visible = world == 1


## 光柱感应区：玩家一靠近就开始白闪过场。
## 单向：已经身在门内世界就再也不响应（门内世界也没有光柱，这里只是双保险）。
func _on_gate_entered(body: Node3D) -> void:
	if body != _player:
		return
	if _transitioning or _current_world == 1:
		return
	_start_transition()


## 过场：白光由远及近盖满屏幕 -> 落点就位 -> 白光淡出。
## 全程不禁用移动键：可以一边走一边看着白光铺开，真正换世界只发生在“白到最白”的那一帧。
## fade 必须先置 1，否则 progress 在涨、整体透明度却是 0，屏幕上什么都不会白。
func _start_transition() -> void:
	_transitioning = true
	if _hud == null:
		_arrive()
		_finish_transition()
		return
	_hud.set_wipe_fade(1.0)
	_hud.set_wipe_progress(0.0)
	var tween := create_tween()
	tween.tween_method(Callable(_hud, "set_wipe_progress"), 0.0, 1.6, WIPE_IN_TIME)
	tween.tween_callback(_arrive)
	tween.tween_method(Callable(_hud, "set_wipe_fade"), 1.0, 0.0, WIPE_OUT_TIME)
	tween.tween_callback(_finish_transition)


## 白到最白的那一刻：换到门内世界，并把人随机丢在落点圆内（朝向也是随机的）。
func _arrive() -> void:
	_apply_world(1)
	if _hud != null:
		_hud.set_world_name(WORLD_NAMES[1])
	if _player == null:
		return
	var origin := _inner_world.global_position
	var angle := randf() * TAU
	# sqrt 是让落点在圆内“面积均匀”，不然会明显都挤在圆心附近。
	var dist := sqrt(randf()) * ARRIVAL_RADIUS
	var point := Vector3(origin.x + cos(angle) * dist, 0.2, origin.z + sin(angle) * dist)
	_player.teleport_to_point(point, randf() * 360.0)
	_cooldown = TELEPORT_COOLDOWN


## 白闪淡出结束：把白幕归零，并允许下一次触发。
func _finish_transition() -> void:
	_transitioning = false
	if _hud != null:
		_hud.set_wipe_progress(0.0)
		_hud.set_wipe_fade(0.0)


func _process(delta: float) -> void:
	_cooldown = maxf(_cooldown - delta, 0.0)

	if _player != null and _player.global_position.y < FALL_LIMIT:
		_respawn()

	# 速度读数每 0.1 秒刷一次就够了，别每帧都拼字符串。
	_speed_accum += delta
	if _speed_accum >= 0.1 and _hud != null and _player != null:
		_speed_accum = 0.0
		_hud.set_speed(_player.get_horizontal_speed())


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
		get_viewport().set_input_as_handled()
		return

	if event is InputEventMouseButton:
		var click := event as InputEventMouseButton
		if click.pressed and Input.mouse_mode != Input.MOUSE_MODE_CAPTURED:
			Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
			get_viewport().set_input_as_handled()
		return

	if event.is_action_pressed("reset_player"):
		_respawn()
		get_viewport().set_input_as_handled()


## 送回当前世界的落点（掉出去或按 R 时用）。
func _respawn() -> void:
	if _player == null:
		return
	_player.teleport_to(_outer_spawn if _current_world == 0 else _inner_spawn)
	_cooldown = TELEPORT_COOLDOWN
	if _hud != null:
		_hud.flash_white()


## 当前所在世界索引（0 = 门外虚空 / 1 = 门内世界）。
func current_world() -> int:
	return _current_world


func _register_fallback_actions() -> void:
	for action_name in FALLBACK_ACTIONS.keys():
		if InputMap.has_action(action_name):
			continue
		InputMap.add_action(action_name)
		for keycode in FALLBACK_ACTIONS[action_name]:
			var key_event := InputEventKey.new()
			key_event.physical_keycode = keycode
			InputMap.action_add_event(action_name, key_event)

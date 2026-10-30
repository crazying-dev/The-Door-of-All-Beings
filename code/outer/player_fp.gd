extends CharacterBody3D
## 第一人称角色：走 / 跑（Shift）/ 跳（Space）+ 鼠标视角。
##
## 约定：节点原点在脚底，胶囊碰撞体向上偏移半个身高，所以摆在 (x, 0, z) 就是站在地面上。
## 头节点（Head）只负责俯仰，角色本体负责左右转身。

@export_group("移动 Movement")
@export var walk_speed: float = 3.6
@export var run_speed: float = 6.8
@export var jump_height: float = 1.1
@export var gravity: float = 22.0
@export var ground_acceleration: float = 26.0
@export var air_acceleration: float = 7.0

@export_group("视角 Camera")
@export var mouse_sensitivity: float = 0.0022
@export var touch_sensitivity: float = 0.0035
@export var pitch_limit_degrees: float = 85.0

const MAX_FALL_SPEED := 60.0

@onready var _head: Node3D = $Head

var _pitch_degrees: float = 0.0

## 触屏控制层（方案B）。故意不写类型标注：它是自定义脚本，静态类型成 Node 会报找不到成员。
## 仅在移动端 / 触屏设备由 world_base.gd 注入；为 null 时本脚本完全按键鼠走。
var touch_controls = null


func _ready() -> void:
	_apply_pitch(0.0)


func _process(_delta: float) -> void:
	# 触屏转向：取走本帧累计的拖拽增量，按 touch_sensitivity 转成视角。
	if touch_controls != null and touch_controls.has_method("take_look"):
		var look: Vector2 = touch_controls.take_look()
		if look != Vector2.ZERO:
			_apply_look(look.x, look.y, touch_sensitivity)


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		var motion := event as InputEventMouseMotion
		_apply_look(motion.relative.x, motion.relative.y, mouse_sensitivity)


## 视角统一入口：鼠标相对位移与触屏拖拽增量都汇到这里，只是灵敏度不同。
func _apply_look(delta_x: float, delta_y: float, sensitivity: float) -> void:
	rotate_y(-delta_x * sensitivity)
	_apply_pitch(_pitch_degrees - rad_to_deg(delta_y * sensitivity))


func _physics_process(delta: float) -> void:
	var input_dir := Input.get_vector("move_left", "move_right", "move_forward", "move_back")
	var sprint := Input.is_action_pressed("sprint")
	# 触屏激活时用摇杆轴覆盖键盘（移动平台上没有键盘），推满行程即疾行。
	if touch_controls != null:
		input_dir = touch_controls.move_axis
		if bool(touch_controls.sprint_held):
			sprint = true
	var direction := Vector3(input_dir.x, 0.0, input_dir.y)
	if direction.length_squared() > 1.0:
		direction = direction.normalized()
	direction = transform.basis * direction
	direction.y = 0.0

	var speed := run_speed if sprint else walk_speed
	var target := direction * speed
	var accel := ground_acceleration if is_on_floor() else air_acceleration
	var horizontal := Vector3(velocity.x, 0.0, velocity.z).move_toward(target, accel * delta)
	velocity.x = horizontal.x
	velocity.z = horizontal.z

	if is_on_floor():
		if Input.is_action_just_pressed("jump") or _wants_touch_jump():
			velocity.y = sqrt(2.0 * gravity * jump_height)
	else:
		velocity.y = maxf(velocity.y - gravity * delta, -MAX_FALL_SPEED)

	move_and_slide()


## 传送到某个落点：位置取 spawn 的原点，朝向取 spawn 的 -Z 方向，俯仰归零。
## 所以“落点朝哪”完全由场景里 Spawn 节点的旋转决定。
func teleport_to(spawn: Node3D) -> void:
	velocity = Vector3.ZERO
	global_position = spawn.global_position
	var forward := -spawn.global_transform.basis.z
	forward.y = 0.0
	if forward.length_squared() > 0.0001:
		look_at(global_position + forward, Vector3.UP)
	_apply_pitch(0.0)


## 落到“某一个点”，朝向用角度（度）指定 —— 门内世界的随机落点用这个。
func teleport_to_point(point: Vector3, yaw_degrees: float) -> void:
	velocity = Vector3.ZERO
	global_position = point
	rotation = Vector3(0.0, deg_to_rad(yaw_degrees), 0.0)
	_apply_pitch(0.0)


## 当前水平速度（m/s），给 HUD 用。
func get_horizontal_speed() -> float:
	return Vector2(velocity.x, velocity.z).length()


## 当前朝向角（度）。0 度对应 -Z 方向（= HUD 里的"北"），顺时针为正。
func get_yaw_degrees() -> float:
	return rad_to_deg(rotation.y)


func _apply_pitch(degrees: float) -> void:
	_pitch_degrees = clampf(degrees, -pitch_limit_degrees, pitch_limit_degrees)
	if _head != null:
		_head.rotation.x = deg_to_rad(_pitch_degrees)


## 触屏跳跃请求（落地时才取用，未取用则保留成跳跃缓冲）。
func _wants_touch_jump() -> bool:
	return touch_controls != null and touch_controls.has_method("take_jump") and touch_controls.take_jump()

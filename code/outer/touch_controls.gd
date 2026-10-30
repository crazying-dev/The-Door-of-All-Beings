extends Control
## 方案B 触屏控制层（仅移动端 / 触屏设备由 world.gd 运行时挂到 HUD 下；键鼠平台不创建本控件）。
##
## 布局（整块全屏 Control，自己用 _input 做多指路由，不靠 GUI 拾取）：
##   * 左半屏：动态虚拟摇杆 —— 手指按下的点即摇杆圆心，拖拽输出 move_axis(-1..1)；
##     推到 SPRINT_ZONE_RATIO 半径以上自动算疾行（省一个跑键）。
##   * 右半屏：按住拖动 = 转向（累计 look 增量，player 每帧取用）。
##   * 右下角：跳跃按钮（圆形，_draw 矢量画，不依赖任何图片素材）。
##
## 输出给 player_fp.gd 轮询：
##   move_axis / sprint_held  直接读属性
##   take_look()              取走并清零累计的视角增量
##   take_jump()              取走一次跳跃请求（落地才消费，天然带跳跃缓冲）
##
## 约定对齐：move_axis 的正负与 Input.get_vector("move_left","move_right","move_forward","move_back")
## 一致 —— x 向右为正、y 向后(屏幕向下)为正、向前为负。player 那边无需区分来源。

const STICK_RADIUS := 100.0        ## 摇杆最大行程（像素）
const SPRINT_ZONE_RATIO := 0.85    ## 推到 85% 行程以上算疾行
const KNOB_RATIO := 0.42           ## 摇杆手柄半径 = STICK_RADIUS * 该比例
const BUTTON_RADIUS := 60.0        ## 跳跃按钮半径（像素）
const BUTTON_HIT_RATIO := 1.35     ## 命中判定放宽倍数（手指不精准）

## —— 供 player 读取的输出状态 ——
var move_axis := Vector2.ZERO
var sprint_held := false
var jump_requested := false

## —— 手指分配（-1 表示该角色当前无手指占用）——
var _stick_finger := -1
var _look_finger := -1
var _stick_base := Vector2.ZERO
var _stick_knob := Vector2.ZERO
var _look_last := Vector2.ZERO
var _look_accum := Vector2.ZERO


func _ready() -> void:
	# 铺满整块视口：本控件自己按坐标分区，mouse_filter 用 IGNORE 完全不抢 GUI 事件。
	set_anchors_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_IGNORE


func _input(event: InputEvent) -> void:
	if event is InputEventScreenTouch:
		if event.pressed:
			_on_press(event.index, event.position)
		else:
			_on_release(event.index)
	elif event is InputEventScreenDrag:
		_on_drag(event.index, event.position, event.relative)


# ---------------------------------------------------------------- 输入路由

func _on_press(index: int, pos: Vector2) -> void:
	# 优先判跳跃按钮（右下角，属于右半屏但要盖过“转向”手势）。
	if _hit_jump(pos):
		jump_requested = true
		queue_redraw()
		return
	var half := _size().x * 0.5
	if pos.x < half:
		# 左半屏：占位摇杆（动态圆心 = 按下点），一只手指控制一个摇杆。
		if _stick_finger == -1:
			_stick_finger = index
			_stick_base = pos
			_stick_knob = pos
			_set_axis(Vector2.ZERO)
			queue_redraw()
	else:
		# 右半屏：转向手势，记录起始位置用于兜底求 delta。
		if _look_finger == -1:
			_look_finger = index
			_look_last = pos


func _on_drag(index: int, pos: Vector2, relative: Vector2) -> void:
	if index == _stick_finger:
		var offset := pos - _stick_base
		if offset.length() > STICK_RADIUS:
			offset = offset.normalized() * STICK_RADIUS
		_stick_knob = _stick_base + offset
		_set_axis(offset)
		queue_redraw()
	elif index == _look_finger:
		# 优先用事件自带的 relative（引擎给的精确位移）；个别平台给 0 时退回手算。
		var delta := relative
		if delta == Vector2.ZERO:
			delta = pos - _look_last
		_look_accum += delta
		_look_last = pos


func _on_release(index: int) -> void:
	if index == _stick_finger:
		_stick_finger = -1
		_set_axis(Vector2.ZERO)
		queue_redraw()
	elif index == _look_finger:
		_look_finger = -1


func _set_axis(offset: Vector2) -> void:
	# offset 已是“像素偏移”，除以行程归一化到 -1..1；y 直接沿用屏幕方向（上=负=前进）。
	move_axis = offset / STICK_RADIUS
	if move_axis.length() > 1.0:
		move_axis = move_axis.normalized()
	sprint_held = offset.length() >= STICK_RADIUS * SPRINT_ZONE_RATIO


# ---------------------------------------------------------------- 供 player 取用

## 取走并清零累计的视角增量（像素）。
func take_look() -> Vector2:
	var look := _look_accum
	_look_accum = Vector2.ZERO
	return look


## 取走一次跳跃请求（消费后置假）。player 只在落地时调用，未取用则保留成跳跃缓冲。
func take_jump() -> bool:
	var jumped := jump_requested
	if jumped:
		jump_requested = false
		queue_redraw()
	return jumped


# ---------------------------------------------------------------- 绘制（矢量，不用素材）

func _draw() -> void:
	var s := _size()
	# 跳跃按钮（右下）。
	var jc := _jump_center()
	var jump_fill := 0.28 if jump_requested else 0.16
	draw_circle(jc, BUTTON_RADIUS, Color(1, 1, 1, jump_fill))
	draw_arc(jc, BUTTON_RADIUS, 0.0, TAU, 40, Color(1, 1, 1, 0.5), 3.0, true)
	_draw_label(jc, "跳")
	# 摇杆：有手指时画活动摇杆；否则在左下画一个半透明提示位，告诉玩家往这儿按。
	if _stick_finger != -1:
		draw_circle(_stick_base, STICK_RADIUS, Color(1, 1, 1, 0.10))
		draw_arc(_stick_base, STICK_RADIUS, 0.0, TAU, 48, Color(1, 1, 1, 0.32), 3.0, true)
		draw_circle(_stick_knob, STICK_RADIUS * KNOB_RATIO, Color(1, 1, 1, 0.55))
	else:
		var hint := Vector2(150.0, s.y - 170.0)
		draw_circle(hint, STICK_RADIUS, Color(1, 1, 1, 0.06))
		draw_arc(hint, STICK_RADIUS, 0.0, TAU, 48, Color(1, 1, 1, 0.18), 2.0, true)


func _draw_label(center: Vector2, text: String) -> void:
	var font := get_theme_default_font()
	if font == null:
		return
	var font_size := 32
	var tw := font.get_string_size(text, HORIZONTAL_ALIGNMENT_CENTER, -1, font_size)
	var base := center + Vector2(-tw.x * 0.5, tw.y * 0.25)
	draw_string(font, base, text, HORIZONTAL_ALIGNMENT_CENTER, -1, font_size, Color(1, 1, 1, 0.85))


func _size() -> Vector2:
	# 布局未就绪时（size 为 0）退回视口尺寸，避免除零与坐标算错。
	if size.x > 0.0 and size.y > 0.0:
		return size
	return get_viewport_rect().size


func _jump_center() -> Vector2:
	var s := _size()
	return Vector2(s.x - 130.0, s.y - 150.0)


func _hit_jump(pos: Vector2) -> bool:
	return pos.distance_to(_jump_center()) <= BUTTON_RADIUS * BUTTON_HIT_RATIO

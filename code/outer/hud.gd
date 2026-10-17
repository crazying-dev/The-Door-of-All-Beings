extends CanvasLayer
## 平视显示：穿界白光（由远及近的擦除）、当前世界名、操作提示、速度读数。
## 全部节点的 mouse_filter 都是 IGNORE，绝不抢鼠标事件（否则鼠标视角会被面板吃掉）。
##
## 这层上有两块白：
##   Flash —— 传统的整体白闪（现在是按 R 复位时闪一下）；
##   Wipe  —— 穿界过场：用 white_wipe.gdshader 让白色从画面中心向四周铺开。

@onready var _flash: ColorRect = $Flash
@onready var _wipe: ColorRect = $Wipe
@onready var _world_label: Label = $WorldLabel
@onready var _hint: Label = $HintLabel
@onready var _speed_label: Label = $InfoPanel/VBox/Speed

var _flash_tween: Tween
var _wipe_material: ShaderMaterial


func _ready() -> void:
	_flash.color = Color(1.0, 1.0, 1.0, 0.0)
	_wipe_material = _wipe.material as ShaderMaterial
	set_wipe_progress(0.0)
	set_wipe_fade(0.0)


## 底部大字：当前所在的世界。
func set_world_name(text: String) -> void:
	_world_label.text = text


## 顶部提示条：传空字符串就隐藏。
func set_hint(text: String) -> void:
	_hint.text = text
	_hint.visible = text != ""


## 左上角速度读数（m/s）。
func set_speed(value: float) -> void:
	_speed_label.text = "速度 %.1f m/s" % value


## 穿界白光：progress 从 0 涨到 1.6。
## 白色先从画面中心（= 远处的光柱 / 地平线）出现，再向四周扩散，直到盖满整屏。
func set_wipe_progress(value: float) -> void:
	if _wipe_material != null:
		_wipe_material.set_shader_parameter("progress", value)


## 盖满之后的整体淡出：1 = 全白，0 = 完全透明（此时玩家已经在门内世界了）。
func set_wipe_fade(value: float) -> void:
	if _wipe_material != null:
		_wipe_material.set_shader_parameter("fade", value)


## 复位时的白光闪一下：先把白色推到全不透明，再 tween 回透明。
func flash_white(duration: float = 0.8) -> void:
	if _flash_tween != null and _flash_tween.is_valid():
		_flash_tween.kill()
	_flash.color = Color(1.0, 1.0, 1.0, 1.0)
	_flash_tween = create_tween()
	_flash_tween.tween_property(_flash, "color:a", 0.0, duration)

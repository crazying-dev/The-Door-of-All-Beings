extends CanvasLayer
## 跨场景过场（autoload）。
##
## 拆成门外大厅 / 门内世界两个独立场景后，切换靠 get_tree().change_scene_to_file()，
## 但换场景会把旧场景整个移除，HUD 里那层白幕（white_wipe）也随之消失，淡出就接不上了。
## 所以把过场做成一个常驻的 autoload CanvasLayer：它不属于任何场景，换场景时不销毁，
## 可以在「盖白 -> 换场景 -> 掀白」全程待命。
##
## 用法：GameTransition.fade_to_scene("res://Scenes/outer/inner_main.tscn")
## 重复触发会被 _busy 挡住，避免连点光柱叠出多层 tween。

const COVER_TIME := 0.9
const REVEAL_TIME := 0.7
const HOLD_TIME := 0.15

var _rect: ColorRect
var _busy: bool = false


func _ready() -> void:
	layer = 100
	process_mode = Node.PROCESS_MODE_ALWAYS
	_rect = ColorRect.new()
	_rect.set_anchors_preset(Control.PRESET_FULL_RECT)
	_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_rect.color = Color(1, 1, 1, 0)
	add_child(_rect)


## 白幕盖满屏幕 -> 换场景 -> 白幕淡出。scene_path 为 res:// 场景路径。
func fade_to_scene(scene_path: String) -> void:
	if _busy:
		return
	_busy = true
	var tween := create_tween()
	tween.tween_property(_rect, "color:a", 1.0, COVER_TIME)
	tween.tween_callback(func() -> void: get_tree().change_scene_to_file(scene_path))
	tween.tween_interval(HOLD_TIME)
	tween.tween_property(_rect, "color:a", 0.0, REVEAL_TIME)
	tween.tween_callback(_set_idle)


func _set_idle() -> void:
	_rect.color.a = 0.0
	_busy = false

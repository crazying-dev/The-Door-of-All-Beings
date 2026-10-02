extends Control

# ============ 临时：进入 race 后倒计时自动跳转到主场景 ============
# TODO(临时)：race 的用户资料设置写好之后，删掉这一段，并把 _ready() 里的
# _temp_auto_goto_main_scene() 调用去掉即可，其余地方不用改。
# 目标场景：res://Scenes/outer/world.tscn（门外世界，游戏本体）
const TEMP_MAIN_SCENE = preload("res://Scenes/outer/world.tscn")
const TEMP_DELAY := 3.0


func _temp_auto_goto_main_scene() -> void:
	print("[临时] race：", TEMP_DELAY, " 秒后自动进入主场景（门外世界）")
	await get_tree().create_timer(TEMP_DELAY).timeout
	get_tree().change_scene_to_packed(TEMP_MAIN_SCENE)
# ============ 临时段结束 ============


# Called when the node enters the scene tree for the first time.
func _ready() -> void:
	_temp_auto_goto_main_scene()


# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	pass

extends "res://code/outer/world_base.gd"
## 门外大厅：纯离线等待界面（固定出生点，不登录 / 不上报坐标 / 不显示其它玩家）。
##
## 拆场景后这里只剩「门外」这一半：灰虚空 + 光柱，走近光柱 -> GameTransition 白幕
## 盖屏 -> 直接换成门内世界场景（inner_main.tscn）。单向，门内没有回门的路。
## 共用逻辑（输入兜底 / 触屏 / 鼠标 / 掉落保护 / 调试面板）全在 world_base.gd。

## 门内世界场景（换场景用的是路径字符串，不能 preload 场景对象，避免循环引用）。
const INNER_SCENE := "res://Scenes/outer/inner_main.tscn"

## 光柱的静态类型故意写成 Node：light_pillar.gd 自定义了 gate_entered 信号，
## 一旦写成 Node3D，connect("gate_entered", ...) 在静态检查阶段就会报“找不到信号”。
@onready var _outer_world: Node3D = $OuterWorld
@onready var _pillar: Node = $OuterWorld/Pillar

## 换场景防重入：感应区可能在白幕期间被反复触发，只认第一次。
var _leaving: bool = false


func _ready() -> void:
	_register_fallback_actions()

	_player = $Player
	_hud = $HUD
	_spawn_point = $Spawn

	# 用字符串形式连接信号：光柱脚本自定义了 gate_entered，静态类型 Node 上找不到它。
	_pillar.connect("gate_entered", _on_gate_entered)

	_setup_touch_controls()
	_setup_mouse()


## 光柱感应区：玩家一靠近就白幕盖屏，换成门内世界场景。
func _on_gate_entered(body: Node3D) -> void:
	if body != _player:
		return
	if _leaving:
		return
	_leaving = true
	GameTransition.fade_to_scene(INNER_SCENE)

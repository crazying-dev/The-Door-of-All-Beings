extends Node3D
## 门内世界（众生之门世界）的程序化装饰。
## 只管“好看”：三层云海 + 悬浮岛 + 漂浮灵灯笼 + 远处山影。
## 全部在 _ready 里用 world_builder.gd 生成，所以改结构没必要动 .tscn，改 export 就行。

const Builder = preload("res://code/outer/world_builder.gd")

@export_group("随机")
@export var seed_value: int = 20210424

@export_group("内容")
@export var island_count: int = 7
@export var lantern_count: int = 10

## 门内世界目前是「只摆一块地板」的简化版：装饰默认关闭。
## 想恢复程序化的云海 / 浮岛 / 灵灯笼，把场景里 InnerWorld 节点的 decorate 勾上即可。
@export var decorate: bool = false

@onready var _decoration: Node3D = $Decoration

var _rng := RandomNumberGenerator.new()
var _lanterns: Array[Node3D] = []
var _time: float = 0.0


func _ready() -> void:
	if not decorate:
		return
	_rng.seed = seed_value
	_build_clouds()
	_build_islands()
	_build_lanterns()
	_build_far_mountains()


func _process(delta: float) -> void:
	_time += delta
	for i in _lanterns.size():
		var lantern: Node3D = _lanterns[i]
		var base_y := float(lantern.get_meta("base_y", lantern.position.y))
		lantern.position.y = base_y + sin(_time * 0.7 + float(i) * 1.3) * 0.5


## 三层云海：半透明大圆盘，越往下越大越淡。全部不投影，否则会遮住太阳。
func _build_clouds() -> void:
	var specs := [
		{"y": -12.0, "r": 60.0, "a": 0.20},
		{"y": -22.0, "r": 92.0, "a": 0.15},
		{"y": -34.0, "r": 130.0, "a": 0.10},
	]
	for i in specs.size():
		var spec: Dictionary = specs[i]
		var mat := Builder.make_translucent(Color(0.86, 0.92, 1.0), float(spec["a"]))
		var cloud := Builder.cylinder(_decoration, "Cloud_%d" % i, float(spec["r"]), 1.2, Vector3(0.0, float(spec["y"]), 0.0), mat, Vector3.ZERO, 48)
		cloud.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF


## 悬浮岛：低多边形（9 边形）扁柱 + 下方的倒锥，散布在灵质台以外的云海里。纯装饰，不做碰撞。
func _build_islands() -> void:
	var rock := Builder.make_material(Color(0.20, 0.24, 0.30))
	var grass := Builder.make_material(Color(0.36, 0.55, 0.36))
	var leaf := Builder.make_material(Color(0.30, 0.46, 0.32))
	for i in island_count:
		var ang := _rng.randf() * TAU
		var dist := _rng.randf_range(75.0, 250.0)
		var y := _rng.randf_range(-34.0, 6.0)
		var r := _rng.randf_range(9.0, 22.0)
		var pos := Vector3(cos(ang) * dist, y, sin(ang) * dist)
		Builder.cylinder(_decoration, "IslandRock_%d" % i, r, r * 0.9, pos + Vector3(0.0, -r * 0.45, 0.0), rock, Vector3.ZERO, 9)
		Builder.cone(_decoration, "IslandSpire_%d" % i, r * 0.7, r * 1.4, pos + Vector3(0.0, -r * 1.5, 0.0), rock, Vector3(180.0, 0.0, 0.0), 9)
		Builder.cylinder(_decoration, "IslandTop_%d" % i, r, r * 0.18, pos, grass, Vector3.ZERO, 9)
		if _rng.randf() < 0.55:
			var tree_pos := pos + Vector3(_rng.randf_range(-r * 0.4, r * 0.4), r * 0.4, _rng.randf_range(-r * 0.4, r * 0.4))
			Builder.cone(_decoration, "IslandTree_%d" % i, r * 0.22, r * 0.9, tree_pos, leaf, Vector3.ZERO, 7)


## 漂浮的灵灯笼：自发光小球；点光源挂在球下，所以上下轻晃时光也跟着晃。
func _build_lanterns() -> void:
	var glow_mat := Builder.make_material(Color(1.0, 0.86, 0.55), 3.0, true)
	for i in lantern_count:
		var ang := _rng.randf() * TAU
		var dist := _rng.randf_range(14.0, 95.0)
		var y := _rng.randf_range(6.0, 22.0)
		var pos := Vector3(cos(ang) * dist, y, sin(ang) * dist)
		var lantern := Builder.sphere(_decoration, "Lantern_%d" % i, 0.55, pos, glow_mat)
		lantern.set_meta("base_y", y)
		lantern.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		_lanterns.append(lantern)
		if i % 2 == 0:
			Builder.omni(lantern, "Light", Vector3.ZERO, Color(1.0, 0.82, 0.50), 2.4, 16.0)


## 远处山影：一圈巨大圆锥，底端埋进云海。
func _build_far_mountains() -> void:
	var far_mat := Builder.make_material(Color(0.22, 0.28, 0.40))
	for i in 9:
		var ang := TAU * float(i) / 9.0 + 0.35
		var dist := _rng.randf_range(230.0, 330.0)
		var h := _rng.randf_range(70.0, 170.0)
		Builder.cone(_decoration, "FarMountain_%d" % i, _rng.randf_range(26.0, 52.0), h, Vector3(cos(ang) * dist, -40.0 + h * 0.5, sin(ang) * dist), far_mat, Vector3.ZERO, 7)

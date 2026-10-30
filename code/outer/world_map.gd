extends Node3D
## 门内世界的地图 + 出生点加载器（运行时构建，不进 .tscn、也不依赖 obj 已被拖进场景）。
##
## 职责：
##   * 加载 res://assets/models/map_v1.obj（200m x 200m 山地）与 map_v1_spawn.obj（出生点平台）；
##   * 地图用 HeightMapShape3D 做「可行走」碰撞（1m 采样栅格，秒建、内存小）；
##   * 出生点平台用一个小 BoxShape3D 碰撞；
##   * 两个模型都挂在本节点（MapRoot）下，共用同一个 world_scale —— 满足「原点叠合、放大需同倍数」约束。
##
## 坐标约定：本节点的局部坐标 = obj 顶点坐标（Godot 的 OBJ 导入已把 Z-up 转成 Y-up，
## 网格顶点即世界空间里的 y 高度）。视觉网格与碰撞都直接建在这套局部坐标上，天然对齐。
##
## 导入产物兼容：load(".obj") 一般返回 PackedScene（内含一个 MeshInstance3D）；
## 少数配置下可能直接是 Mesh。两种都处理；拿不到就整体跳过（未导入前也不报错）。

## 地图 / 出生点模型路径（英文目录，避免 res:// 含非 ASCII）。
const MAP_PATH := "res://assets/models/map_v1.obj"
const SPAWN_PATH := "res://assets/models/map_v1_spawn.obj"

## 地图与出生点共用的整体缩放。1 单位 = 1 米时 200m x 200m，默认 1.0。
@export var world_scale: float = 1.0
## 高度图采样间距（米）。Godot 的 HeightMapShape3D 采样间距固定 1m，此处与之绑死；
## 若想改粗细只改 map 分辨率（max_grid_side），不要改这个值，否则碰撞会与视觉错位。
const SAMPLE_SPACING := 1.0
## 栅格单边最大格数（防御性上限，避免异常大的包围盒把内存吃爆）。
@export var max_grid_side: int = 400

## 地图是否加载成功（inner_world / world 据此决定是否移除占位地板、把出生点抬到地面）。
var loaded: bool = false

## 高度图缓存（供 height_at 查询）。
var _heights: PackedFloat32Array = PackedFloat32Array()
var _grid_w: int = 0
var _grid_d: int = 0
var _min_x: float = 0.0
var _min_z: float = 0.0


func _ready() -> void:
	scale = Vector3(world_scale, world_scale, world_scale)
	loaded = _load_map()
	if loaded:
		load_spawn_platform()


## 返回地图网格在 MapRoot 局部空间的高度（米）；未加载/越界则回落到 base（0）。
func height_at(local_x: float, local_z: float) -> float:
	if not loaded or _grid_w <= 0 or _grid_d <= 0:
		return 0.0
	# 越界用最近边缘格（地形边缘外也是地面，别把人物抛到 0 以下）。
	var i := clampi(int(round((local_x - _min_x) / SAMPLE_SPACING)), 0, _grid_w - 1)
	var j := clampi(int(round((local_z - _min_z) / SAMPLE_SPACING)), 0, _grid_d - 1)
	return _heights[i * _grid_d + j]


## 给定世界坐标，返回其正下方的地面高度（世界 Y）。给 _arrive() 摆放出生点用。
func ground_y_at_global(global_pos: Vector3) -> float:
	var local := to_local(global_pos)
	var h := height_at(local.x, local.z)
	# 把局部高度换算回世界 Y（含 MapRoot 的 scale / 位移）。
	return to_global(Vector3(local.x, h, local.z)).y


## 加载地图：建视觉网格 + 高度图碰撞；成功返回 true。
func _load_map() -> bool:
	var mesh := _extract_mesh(MAP_PATH)
	if mesh == null:
		push_warning("门内地图未就绪（%s 尚未导入或不是 ArrayMesh），已跳过。" % MAP_PATH)
		return false

	# 视觉：直接用一个 MeshInstance3D 承载网格（identity 变换，坐标即顶点坐标）。
	var visual := MeshInstance3D.new()
	visual.name = "MapVisual"
	visual.mesh = mesh
	add_child(visual)

	# 碰撞：扫描顶点建高度图栅格。
	if not _build_height_collision(mesh):
		return false
	return true


## 从 mesh 顶点建 HeightMapShape3D 并挂 StaticBody3D。成功返回 true。
func _build_height_collision(mesh: ArrayMesh) -> bool:
	var arrays: Array = mesh.surface_get_arrays(0)
	if arrays == null or arrays.size() <= Mesh.ARRAY_VERTEX:
		push_warning("地图表面数据缺少顶点数组，跳过碰撞。")
		return false
	var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	if verts.size() == 0:
		return false

	# 第一遍：求 X/Z 包围与最低 y（作为未覆盖格子的兜底高度）。
	_min_x = INF
	_min_z = INF
	var max_x := -INF
	var max_z := -INF
	var base_y := INF
	for v in verts:
		if v.x < _min_x: _min_x = v.x
		if v.x > max_x: max_x = v.x
		if v.z < _min_z: _min_z = v.z
		if v.z > max_z: max_z = v.z
		if v.y < base_y: base_y = v.y
	if not is_finite(_min_x) or not is_finite(max_x):
		return false

	_grid_w = clampi(int(round((max_x - _min_x) / SAMPLE_SPACING)) + 1, 1, max_grid_side)
	_grid_d = clampi(int(round((max_z - _min_z) / SAMPLE_SPACING)) + 1, 1, max_grid_side)

	# 初始化为 base_y（地形以下就是这块底），命中处取最高 y（heightfield 单值，取顶）。
	_heights = PackedFloat32Array()
	_heights.resize(_grid_w * _grid_d)
	for k in _heights.size():
		_heights[k] = base_y
	var hit := PackedByteArray()
	hit.resize(_grid_w * _grid_d)
	for v in verts:
		var i := int(round((v.x - _min_x) / SAMPLE_SPACING))
		var j := int(round((v.z - _min_z) / SAMPLE_SPACING))
		if i < 0 or i >= _grid_w or j < 0 or j >= _grid_d:
			continue
		var idx := i * _grid_d + j
		if v.y > _heights[idx]:
			_heights[idx] = v.y
		hit[idx] = 1
	_fill_gaps(hit)

	var shape := HeightMapShape3D.new()
	shape.map_width = _grid_w
	shape.map_depth = _grid_d
	shape.map_data = _heights

	# HeightMapShape3D 的采样点以节点原点为中心、间距 1m：格 (i,j) 位于
	#   x = node.x + (i - W/2) * spacing。要让它对齐到 min_x + i*spacing，
	#   就把节点放到 x = min_x + W/2*spacing（z 同理）。y=0（高度是绝对值）。
	var body := StaticBody3D.new()
	body.name = "MapBody"
	body.collision_layer = 1
	body.collision_mask = 0
	var cs := CollisionShape3D.new()
	cs.name = "MapCollision"
	cs.shape = shape
	cs.position = Vector3(
		_min_x + float(_grid_w) * 0.5 * SAMPLE_SPACING,
		0.0,
		_min_z + float(_grid_d) * 0.5 * SAMPLE_SPACING,
	)
	body.add_child(cs)
	add_child(body)
	return true


## 把没被顶点命中的格子用邻域平均补上，避免内部小空洞把人物抛到底。
func _fill_gaps(hit: PackedByteArray) -> void:
	var has_gap := false
	for k in hit.size():
		if hit[k] == 0:
			has_gap = true
			break
	if not has_gap:
		return
	# 有限次扩散填充（边缘外的空格保持 base_y 不动）。
	for _pass in 8:
		var changed := false
		for i in _grid_w:
			for j in _grid_d:
				var idx := i * _grid_d + j
				if hit[idx] != 0:
					continue
				var sum := 0.0
				var cnt := 0
				if i > 0 and hit[idx - _grid_d] != 0:
					sum += _heights[idx - _grid_d]
					cnt += 1
				if i < _grid_w - 1 and hit[idx + _grid_d] != 0:
					sum += _heights[idx + _grid_d]
					cnt += 1
				if j > 0 and hit[idx - 1] != 0:
					sum += _heights[idx - 1]
					cnt += 1
				if j < _grid_d - 1 and hit[idx + 1] != 0:
					sum += _heights[idx + 1]
					cnt += 1
				if cnt > 0:
					_heights[idx] = sum / float(cnt)
					hit[idx] = 1
					changed = true
		if not changed:
			break


## 出生点平台：视觉 + 轻量 Box 碰撞（小模型，几十面，直接按包围盒挡一下即可）。
func load_spawn_platform() -> void:
	var mesh := _extract_mesh(SPAWN_PATH)
	if mesh == null:
		push_warning("出生点平台未就绪（%s 尚未导入），跳过。" % SPAWN_PATH)
		return
	var visual := MeshInstance3D.new()
	visual.name = "SpawnVisual"
	visual.mesh = mesh
	add_child(visual)

	var aabb := mesh.get_aabb()
	var body := StaticBody3D.new()
	body.name = "SpawnBody"
	body.collision_layer = 1
	body.collision_mask = 0
	var cs := CollisionShape3D.new()
	cs.name = "SpawnCollision"
	var box := BoxShape3D.new()
	box.size = aabb.size
	cs.shape = box
	cs.position = aabb.position + aabb.size * 0.5
	body.add_child(cs)
	add_child(body)


## 从 .obj 路径取 ArrayMesh：兼容「导入成 PackedScene」与「直接是 Mesh」两种产物。
func _extract_mesh(path: String) -> ArrayMesh:
	var res: Resource = load(path)
	if res == null:
		return null
	var mesh: Mesh = null
	if res is PackedScene:
		var inst := (res as PackedScene).instantiate()
		mesh = _find_first_mesh(inst)
		inst.free()
	elif res is Mesh:
		mesh = res as Mesh
	if mesh is ArrayMesh:
		return mesh as ArrayMesh
	if mesh != null:
		push_warning("%s 不是 ArrayMesh（%s），无法读取顶点建碰撞。" % [path, mesh.get_class()])
	return null


## 递归找第一个带 mesh 的 MeshInstance3D。
func _find_first_mesh(node: Node) -> Mesh:
	if node is MeshInstance3D:
		var mi := node as MeshInstance3D
		if mi.mesh != null:
			return mi.mesh
	for child in node.get_children():
		var found := _find_first_mesh(child)
		if found != null:
			return found
	return null

extends Node3D
## 门内世界的地图 + 出生点加载器（运行时构建，不进 .tscn、也不依赖 obj 已被拖进场景）。
##
## 职责：
##   * 加载 res://assets/models/map_v1.obj（200m x 200m 山地）与 map_v1_spawn.obj（出生点平台）；
##   * 地图用 HeightMapShape3D 做「可行走」碰撞（1m 采样栅格，秒建、内存小）；
##   * 出生点平台用一个小 BoxShape3D 碰撞；
##   * 两个模型都挂在本节点（MapRoot）下，共用同一个 world_scale —— 满足「原点叠合、放大需同倍数」约束。
##
## 坐标约定：本节点的局部坐标 = obj 顶点坐标（模型已确认 Y-up：200(X) x 98.5(Y高) x 200(Z)，
## 底面 y=0）。视觉网格与碰撞都直接建在这套局部坐标上，天然对齐。
##
## 导入产物兼容：本项目的 .obj 导入模式产出的直接是 Mesh（见 map_v1.obj.import type="Mesh"）；
## 兼容 PackedScene 产物时取全部 MeshInstance3D（导入器按材质/平滑组可能拆成多个，只取
## 第一个会漏掉整块地图）。拿不到就整体跳过（未导入前也不报错）。

## 地图 / 出生点模型路径（英文目录，避免 res:// 含非 ASCII）。
const MAP_PATH := "res://assets/models/map_v1.obj"
const SPAWN_PATH := "res://assets/models/map_v1_spawn.obj"

## 地图与出生点共用的整体缩放。1 单位 = 1 米时 200m x 200m，默认 1.0。
@export var world_scale: float = 1.0
## 高度图采样间距（米）。Godot 的 HeightMapShape3D 采样间距固定 1m，此处与之绑死；
## 若想改粗细只改 map 分辨率（max_grid_side），不要改这个值，否则碰撞会与视觉错位。
const SAMPLE_SPACING := 1.0
## 栅格单边最大格数（防御性上限，避免异常大的包围盒把内存吃爆）。
## 200m 地图需要 401 格，旧值 400 会静默截断最后一列并错位其余顶点。
@export var max_grid_side: int = 512

## 地图是否加载成功（inner_world / 各场景据此决定是否移除占位地板、把出生点抬到地面）。
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


## 返回地图网格在 MapRoot 局部空间的高度（米）；未加载返回 0。
## 越界用最近边缘格；无任何三角形覆盖的孔洞格返回 NaN（人会从这里掉下去，
## 由 FALL_LIMIT 掉落保护接住送回出生点 —— 比伪造一块贴地的隐形地板更真实）。
func height_at(local_x: float, local_z: float) -> float:
	if not loaded or _grid_w <= 0 or _grid_d <= 0:
		return 0.0
	var i := clampi(int(round((local_x - _min_x) / SAMPLE_SPACING)), 0, _grid_w - 1)
	var j := clampi(int(round((local_z - _min_z) / SAMPLE_SPACING)), 0, _grid_d - 1)
	return _heights[i * _grid_d + j]


## 给定世界坐标，返回其正下方的地面高度（世界 Y）。给摆放出生点 / 复位用。
## 孔洞格（NaN）原样传出去，调用方用 is_finite 判断后回落。
func ground_y_at_global(global_pos: Vector3) -> float:
	var local := to_local(global_pos)
	var h := height_at(local.x, local.z)
	if is_nan(h):
		return h
	# 把局部高度换算回世界 Y（含 MapRoot 的 scale / 位移）。
	return to_global(Vector3(local.x, h, local.z)).y


## 加载地图：建视觉网格 + 高度图碰撞；成功返回 true。
func _load_map() -> bool:
	var mesh := _extract_mesh(MAP_PATH)
	if mesh == null:
		push_warning("门内地图未就绪（%s 尚未导入），已跳过。" % MAP_PATH)
		return false

	# 视觉：直接用一个 MeshInstance3D 承载网格（identity 变换，坐标即顶点坐标）。
	var visual := MeshInstance3D.new()
	visual.name = "MapVisual"
	visual.mesh = mesh
	add_child(visual)
	_ensure_material(mesh, visual)

	# 碰撞：扫描顶点建高度图栅格。
	if not _build_height_collision(mesh):
		return false
	return true


## obj 没带材质（MTL 空文件 / 无 usemtl）时给一个中性灰材质兜底，
## 避免出现无材质表面在不同渲染后端下表现不一致（部分区域发黑或不显示）。
## 只补没有材质的表面，已经带材质的不动。
func _ensure_material(mesh: Mesh, visual: MeshInstance3D) -> void:
	var any_material := false
	for s in mesh.get_surface_count():
		if mesh.surface_get_material(s) != null:
			any_material = true
			break
	if any_material:
		return
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(0.5, 0.52, 0.55)
	# material_override 对全部表面生效，也不用担心表面数量。
	visual.material_override = mat


## 从 mesh 顶点建 HeightMapShape3D 并挂 StaticBody3D。成功返回 true。
## 扫全部表面：导入器按材质 / 平滑组拆分时只读 0 号表面会丢一大块碰撞。
func _build_height_collision(mesh: Mesh) -> bool:
	var surface_count := mesh.get_surface_count()
	if surface_count == 0:
		return false

	# 顶点数组只取一次缓存住：surface_get_arrays 对压缩网格是解压操作，
	# 百万顶点的 mesh 重复取两遍会明显拖慢进场。
	# 故意不写类型标注：取不到顶点的表面存 null（PackedVector3Array 装不了 null）。
	var surfaces := []
	for s in surface_count:
		surfaces.append(_surface_vertices(mesh, s))

	# 第一遍：跨表面求 X/Z 包围。
	_min_x = INF
	_min_z = INF
	var max_x := -INF
	var max_z := -INF
	var total_verts := 0
	for verts in surfaces:
		if verts == null:
			continue
		total_verts += verts.size()
		for v in verts:
			if v.x < _min_x: _min_x = v.x
			if v.x > max_x: max_x = v.x
			if v.z < _min_z: _min_z = v.z
			if v.z > max_z: max_z = v.z
	if total_verts == 0 or not is_finite(_min_x) or not is_finite(max_x):
		push_warning("地图表面数据缺少顶点数组，跳过碰撞。")
		return false

	_grid_w = clampi(int(round((max_x - _min_x) / SAMPLE_SPACING)) + 1, 1, max_grid_side)
	_grid_d = clampi(int(round((max_z - _min_z) / SAMPLE_SPACING)) + 1, 1, max_grid_side)

	# 初始化为 NaN（= 无覆盖孔洞，让人掉下去），命中处取最高 y（heightfield 单值，取顶）。
	var cell_count := _grid_w * _grid_d
	_heights = PackedFloat32Array()
	_heights.resize(cell_count)
	for k in cell_count:
		_heights[k] = NAN
	var hit := PackedByteArray()
	hit.resize(cell_count)
	for verts in surfaces:
		if verts == null:
			continue
		for v in verts:
			var i := int(round((v.x - _min_x) / SAMPLE_SPACING))
			var j := int(round((v.z - _min_z) / SAMPLE_SPACING))
			if i < 0 or i >= _grid_w or j < 0 or j >= _grid_d:
				continue
			var idx := i * _grid_d + j
			if hit[idx] == 0 or v.y > _heights[idx]:
				_heights[idx] = v.y
			hit[idx] = 1
	_fill_gaps(hit)
	# 扩散 8 轮仍未覆盖的格子保持「洞」：压成 -100（远低于 FALL_LIMIT=-40），
	# 玩家踩上去会掉出去被掉落保护送回出生点，而不是被一块隐形地板托住。
	for k in cell_count:
		if hit[k] == 0:
			_heights[k] = -100.0

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


## 取某表面的顶点数组；不支持 ArrayMesh 语义的网格（压缩格式等）返回 null。
## 故意不写返回类型标注：GDScript 不允许在 PackedVector3Array 返回类型下 return null。
func _surface_vertices(mesh: Mesh, surface: int):
	var array_mesh := mesh as ArrayMesh
	if array_mesh == null:
		return null
	var arrays: Array = array_mesh.surface_get_arrays(surface)
	if arrays == null or arrays.size() <= Mesh.ARRAY_VERTEX:
		return null
	var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	if verts.size() == 0:
		return null
	return verts


## 把没被顶点命中的格子用邻域平均补上，避免地形内部小空洞把人物抛下去。
## 补不到的（地形以外的空格）保持「洞」，见 _build_height_collision 末尾。
func _fill_gaps(hit: PackedByteArray) -> void:
	var has_gap := false
	for k in hit.size():
		if hit[k] == 0:
			has_gap = true
			break
	if not has_gap:
		return
	# 有限次扩散填充（只从有邻居的格子向外蔓延，一次 8 轮足够盖住格网毛刺）。
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
	_ensure_material(mesh, visual)

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


## 从 .obj 路径取 Mesh：兼容「导入成 Mesh」（本项目 .import 的 type="Mesh"）与
## 「导入成 PackedScene」两种产物。PackedScene 产物取全部 MeshInstance3D 合并 ——
## 导入器可能按材质 / 平滑组拆成多个节点，只取第一个会漏掉地图。
func _extract_mesh(path: String) -> Mesh:
	var res: Resource = load(path)
	if res == null:
		return null
	if res is Mesh:
		return res as Mesh
	if res is PackedScene:
		var inst := (res as PackedScene).instantiate()
		var meshes := []
		_collect_meshes(inst, meshes)
		inst.free()
		if meshes.size() == 1:
			return meshes[0]
		if meshes.size() > 1:
			var combined := ArrayMesh.new()
			for m in meshes:
				combined.append_surface_from_arrays(
					m.get_primitive_type(), m.surface_get_arrays(0),
					m.surface_get_material(0))
			return combined
	push_warning("%s 不是可识别的 Mesh 导入产物，已跳过。" % path)
	return null


## 递归收集节点树里所有带 mesh 的 MeshInstance3D。
func _collect_meshes(node: Node, out: Array) -> void:
	if node is MeshInstance3D:
		var mi := node as MeshInstance3D
		if mi.mesh != null:
			out.append(mi.mesh)
	for child in node.get_children():
		_collect_meshes(child, out)

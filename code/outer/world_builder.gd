extends RefCounted
## 程序化建造小工具：全部是 static 函数，preload 后直接调用，不需要 class_name 全局注册。
## 统一约定：parent 必须是已经在场景树里的 Node3D，位置/旋转都用本地坐标。


## 只带颜色（可选自发光 / 不参与光照）的基础材质。
static func make_material(color: Color, emission: float = 0.0, unshaded: bool = false) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	if emission > 0.0:
		mat.emission_enabled = true
		mat.emission = color
		mat.emission_energy_multiplier = emission
	if unshaded:
		mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	return mat


## 半透明材质（云海 / 光幕用）。
static func make_translucent(color: Color, alpha: float, unshaded: bool = true) -> StandardMaterial3D:
	var mat := make_material(color, 0.0, unshaded)
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.albedo_color = Color(color.r, color.g, color.b, alpha)
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	return mat


## 方块。rot_deg 用角度制，方便直接写“翘 18 度”这种数。
static func box(parent: Node3D, node_name: String, size: Vector3, pos: Vector3, material: Material, rot_deg: Vector3 = Vector3.ZERO) -> MeshInstance3D:
	var mesh := BoxMesh.new()
	mesh.size = size
	mesh.material = material
	var node := MeshInstance3D.new()
	node.name = node_name
	node.mesh = mesh
	node.position = pos
	node.rotation_degrees = rot_deg
	parent.add_child(node)
	return node


## 圆柱 / 圆盘（top_radius=0 就是圆锥，拿来做远山）。
static func cylinder(parent: Node3D, node_name: String, radius: float, height: float, pos: Vector3, material: Material, rot_deg: Vector3 = Vector3.ZERO, segments: int = 24) -> MeshInstance3D:
	return _cone(parent, node_name, radius, radius, height, pos, material, rot_deg, segments)


## 圆锥（底部半径 radius，顶部半径 0）。
static func cone(parent: Node3D, node_name: String, radius: float, height: float, pos: Vector3, material: Material, rot_deg: Vector3 = Vector3.ZERO, segments: int = 6) -> MeshInstance3D:
	return _cone(parent, node_name, 0.0, radius, height, pos, material, rot_deg, segments)


static func _cone(parent: Node3D, node_name: String, top_radius: float, bottom_radius: float, height: float, pos: Vector3, material: Material, rot_deg: Vector3, segments: int) -> MeshInstance3D:
	var mesh := CylinderMesh.new()
	mesh.top_radius = top_radius
	mesh.bottom_radius = bottom_radius
	mesh.height = height
	mesh.radial_segments = segments
	mesh.rings = 1
	mesh.material = material
	var node := MeshInstance3D.new()
	node.name = node_name
	node.mesh = mesh
	node.position = pos
	node.rotation_degrees = rot_deg
	parent.add_child(node)
	return node


## 球（悬浮灯笼 / 浮石）。
static func sphere(parent: Node3D, node_name: String, radius: float, pos: Vector3, material: Material) -> MeshInstance3D:
	var mesh := SphereMesh.new()
	mesh.radius = radius
	mesh.height = radius * 2.0
	mesh.radial_segments = 20
	mesh.rings = 12
	mesh.material = material
	var node := MeshInstance3D.new()
	node.name = node_name
	node.mesh = mesh
	node.position = pos
	parent.add_child(node)
	return node


## 点光源（悬浮灯笼用）。
static func omni(parent: Node3D, node_name: String, pos: Vector3, color: Color, energy: float, light_range: float) -> OmniLight3D:
	var light := OmniLight3D.new()
	light.name = node_name
	light.position = pos
	light.light_color = color
	light.light_energy = energy
	light.omni_range = light_range
	light.shadow_enabled = false
	parent.add_child(light)
	return light


## 不投影的静态碰撞体（门柱 / 石碑这类装饰性阻挡）。
static func static_box(parent: Node3D, node_name: String, size: Vector3, pos: Vector3, layer: int = 1) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = node_name
	body.collision_layer = layer
	body.collision_mask = 0
	var shape := CollisionShape3D.new()
	var box_shape := BoxShape3D.new()
	box_shape.size = size
	shape.shape = box_shape
	shape.position = pos
	body.add_child(shape)
	parent.add_child(body)
	return body

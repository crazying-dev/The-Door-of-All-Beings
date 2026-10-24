extends Node3D
## 线条骨架播放器（line rig）
##
## 作用：把一棵「骨骼层级」用 1 像素线段画出来，每个关节再放一颗小球，
## 动作交给 AnimationPlayer 的关键帧驱动 —— 方便以后套到联机玩家 / NPC 身上。
##
## 复用步骤：
##   1. 把本脚本挂到一个 Node3D 上（通常就是场景根）；
##   2. 场景里摆一棵骨骼树：每根骨骼都是 Node3D，加入分组 rig_bone，
##      骨骼的 position 就是「相对父关节的偏移」，旋转随意；
##   3. 放一个 AnimationPlayer，关键帧路径写「<骨骼节点路径>:rotation_degrees」；
##   4. 运行时调用 play_motion("walk") 切换动作。
##
## 脚本自动完成这些事：
##   · 骨骼 → 子骨骼连线（ImmediateMesh + PRIMITIVE_LINES，恒定 1 像素宽）；
##   · 每个关节一颗小球（运行时生成，挂在骨骼下面，自动跟随变换）；
##   · head_bone 用一个三圈线框球代替小球（三个正交圆环，从任何角度都不会变成一条线）；
##   · 用数字键 1 / 2 / 3 预览 idle / walk / run。
## 场景里因此不需要任何 Mesh / 材质资源，换骨架只要改节点树。

@export_group("骨架 Rig")
## 骨骼树的根。分组 rig_bone 的节点不必是它的直接子节点，中间可以夹其它节点。
@export var rig_root: NodePath = ^"Rig"
## 骨骼分组名：只有这个分组里的 Node3D 才被当成骨骼。
@export var bone_group: StringName = &"rig_bone"
## 播放动作用的 AnimationPlayer；找不到就只画静态骨架。
@export var animation_player: NodePath = ^"AnimationPlayer"
## 进入场景后自动播放的动作；留空 = 保持场景里的初始姿势。
@export var default_motion: StringName = &"idle"

@export_group("外观 Look")
@export var line_color: Color = Color(0.62, 0.93, 1, 1)
@export var joint_color: Color = Color(1, 0.72, 0.32, 1)
@export var joint_radius: float = 0.024
@export var draw_joints: bool = true
## 用线框球代替关节小球的骨骼名（默认是头）。
@export var head_bone: StringName = &"Head"
@export var head_radius: float = 0.095
@export var head_segments: int = 14
## 圆心相对 head_bone 原点的偏移（沿骨骼自身坐标，+Y = 往头顶）。
@export var head_offset: Vector3 = Vector3(0, 0.09, 0)
## ImmediateMesh 的 AABB 不会自动更新，给点余量免得被视锥裁掉。
@export var cull_margin: float = 8.0

@export_group("预览 Preview")
## 允许用数字键 1 / 2 / 3 在 idle / walk / run 之间切换（编辑器里看动画很方便）。
@export var preview_keys: bool = true

var _mesh: ImmediateMesh
var _mesh_instance: MeshInstance3D
var _line_material: StandardMaterial3D
var _joint_material: StandardMaterial3D
var _joint_mesh: SphereMesh
var _bones: Array[Node3D] = []
## Array[[父骨骼, 子骨骼]]，每帧按它连线。
var _segments: Array = []
var _head: Node3D
var _anim: AnimationPlayer
var _current_motion: StringName = &""


func _ready() -> void:
	# 关键：网格必须在 AnimationPlayer 更新骨骼之后再重建，
	# 否则线条会比关节小球落后一帧（帧率越低错位越明显）。
	process_priority = 100
	_anim = get_node_or_null(animation_player) as AnimationPlayer
	_build_materials()
	_collect()
	_create_mesh_instance()
	_create_joints()
	if default_motion != &"":
		play_motion(default_motion, 0.0)


func _process(_delta: float) -> void:
	rebuild()


func _unhandled_input(event: InputEvent) -> void:
	if not preview_keys:
		return
	if event is InputEventKey and event.pressed and not event.echo:
		match (event as InputEventKey).keycode:
			KEY_1:
				play_motion(&"idle")
			KEY_2:
				play_motion(&"walk")
			KEY_3:
				play_motion(&"run")


## 切换动作。动作不存在时返回 false（不报错，方便外部无脑调用）。
func play_motion(motion: StringName, blend_sec: float = 0.2) -> bool:
	if _anim == null or not _anim.has_animation(motion):
		return false
	if _current_motion == motion and _anim.is_playing():
		return true
	_anim.play(motion, blend_sec)
	_current_motion = motion
	return true


func current_motion() -> StringName:
	return _current_motion


## 按名字取骨骼节点（找不到返回 null），方便外部单独驱动某根骨头。
func get_bone(bone_name: StringName) -> Node3D:
	for bone in _bones:
		if bone.name == bone_name:
			return bone
	return null


## 全部骨骼节点（拷贝，随便改不会影响内部状态）。
func get_bones() -> Array[Node3D]:
	return _bones.duplicate()


## 骨架整体显隐（线条 + 关节一起）。
func set_rig_visible(value: bool) -> void:
	if _mesh_instance != null:
		_mesh_instance.visible = value
	for bone in _bones:
		var joint := bone.get_node_or_null("Joint")
		if joint is MeshInstance3D:
			(joint as MeshInstance3D).visible = value


## 重新生成线条网格。动画每帧都会改骨骼变换，所以 _process 里每帧调一次。
func rebuild() -> void:
	if _mesh == null:
		return
	_mesh.clear_surfaces()
	_mesh.surface_begin(Mesh.PRIMITIVE_LINES, _line_material)
	var to_local_basis := _mesh_instance.global_transform.affine_inverse()
	for segment in _segments:
		var from_node: Node3D = segment[0]
		var to_node: Node3D = segment[1]
		_add_line(to_local_basis * from_node.global_position, to_local_basis * to_node.global_position)
	if _head != null:
		_add_head_wireframe(to_local_basis)
	_mesh.surface_end()


func _build_materials() -> void:
	_line_material = StandardMaterial3D.new()
	_line_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_line_material.albedo_color = line_color
	_line_material.cull_mode = BaseMaterial3D.CULL_DISABLED

	_joint_material = StandardMaterial3D.new()
	_joint_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_joint_material.albedo_color = joint_color

	_joint_mesh = SphereMesh.new()
	_joint_mesh.radius = joint_radius
	_joint_mesh.height = joint_radius * 2.0
	_joint_mesh.radial_segments = 10
	_joint_mesh.rings = 6
	_joint_mesh.material = _joint_material


## 收集骨骼与「父 → 子」连线对。
func _collect() -> void:
	_bones.clear()
	_segments.clear()
	_head = null
	var root := get_node_or_null(rig_root)
	if root == null:
		push_warning("player_rig: 找不到 rig_root（%s）" % rig_root)
		return
	_walk_tree(root)
	if head_bone != &"":
		_head = get_bone(head_bone)


func _walk_tree(node: Node) -> void:
	for child in node.get_children():
		if child is Node3D and child.is_in_group(bone_group):
			if node is Node3D and node.is_in_group(bone_group):
				_segments.append([node as Node3D, child as Node3D])
			_bones.append(child as Node3D)
		_walk_tree(child)


func _create_mesh_instance() -> void:
	_mesh = ImmediateMesh.new()
	_mesh_instance = MeshInstance3D.new()
	_mesh_instance.name = "RigLines"
	_mesh_instance.mesh = _mesh
	_mesh_instance.extra_cull_margin = cull_margin
	_mesh_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(_mesh_instance)


func _create_joints() -> void:
	if not draw_joints:
		return
	for bone in _bones:
		if bone == _head:
			continue
		var joint := MeshInstance3D.new()
		joint.name = "Joint"
		joint.mesh = _joint_mesh
		joint.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		bone.add_child(joint)


func _add_line(from_point: Vector3, to_point: Vector3) -> void:
	_mesh.surface_add_vertex(from_point)
	_mesh.surface_add_vertex(to_point)


## 头用三圈线框球表示：圆心在 head 骨骼的 head_offset，三个正交圆环拼成一个球，
## 侧着看也不会变成一条细线。
func _add_head_wireframe(to_local_basis: Transform3D) -> void:
	var head_transform := _head.global_transform
	var center := head_transform * head_offset
	var axis_x := head_transform.basis.x.normalized()
	var axis_y := head_transform.basis.y.normalized()
	var axis_z := head_transform.basis.z.normalized()
	var segments := maxi(head_segments, 3)
	_add_ring(to_local_basis, center, axis_x, axis_y, segments)
	_add_ring(to_local_basis, center, axis_y, axis_z, segments)
	_add_ring(to_local_basis, center, axis_z, axis_x, segments)


func _add_ring(to_local_basis: Transform3D, center: Vector3, axis_a: Vector3, axis_b: Vector3, segments: int) -> void:
	var radius_a := axis_a * head_radius
	var radius_b := axis_b * head_radius
	for i in segments:
		var angle_from := TAU * float(i) / float(segments)
		var angle_to := TAU * float(i + 1) / float(segments)
		var point_from := center + radius_a * cos(angle_from) + radius_b * sin(angle_from)
		var point_to := center + radius_a * cos(angle_to) + radius_b * sin(angle_to)
		_add_line(to_local_basis * point_from, to_local_basis * point_to)

extends "res://code/outer/world_base.gd"
## 门内世界：地图 + 联机的一半。
##
## 从大厅穿过光柱后被换进这个场景：出生点平台 + 高度图地形（world_map 在
## inner_world.gd 里运行时构建）+ 阳光。联机同步层在这里第一次拉起
## （登录 / 上报坐标 / 其它玩家代理 / 「上次退出位置」恢复都在 net_sync 内部完成，
## 登录拿到存档坐标后会覆盖下面这个首帧落点）。
## 共用逻辑（输入兜底 / 触屏 / 鼠标 / 掉落保护 / 调试面板）全在 world_base.gd。

@onready var _inner_world: Node3D = $InnerWorld


func _ready() -> void:
	_register_fallback_actions()

	_player = $Player
	_hud = $HUD
	_spawn_point = $InnerWorld/Spawn

	_place_at_spawn()
	_setup_touch_controls()
	_setup_mouse()
	# 进门这一刻才启动联机同步：门内世界才联机。
	_setup_net_sync()


## 把人放到出生点平台中心（InnerWorld 原点，两模型原点叠合处）。
## 有地形时按地面高度从上方落下；无地形（地图未导入）时按占位地板高度站住。
func _place_at_spawn() -> void:
	if _player == null:
		return
	var center := _inner_world.global_position
	var ground_y := center.y
	# world_map 是 inner_world.gd 的自定义属性，静态类型 Node3D 上不存，故用 Object.get 取。
	var wm = _inner_world.get("world_map")
	if wm != null and wm.has_method("ground_y_at_global"):
		var probed := float(wm.call("ground_y_at_global", center))
		# 孔洞格会返回 NaN（掉出去交给掉落保护），落点计算不能拿去用，必须过滤。
		if is_finite(probed):
			ground_y = probed
	# 出生点平台顶面约 0.2m，再抬 1.5m 让人物从上方自落到平台上。
	var point := Vector3(center.x, ground_y + 1.5, center.z)
	_player.teleport_to_point(point, randf() * 360.0)
	_cooldown = TELEPORT_COOLDOWN


## 复位 / 掉出地板：重新按地形高度放回出生平台（比直接 teleport 到 Spawn 节点更稳，
## 地图落地后 Spawn 节点悬在半空也站不住）。
func _respawn() -> void:
	_place_at_spawn()
	if _hud != null:
		_hud.flash_white()

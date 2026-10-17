extends Node3D
## 众生之门的入口：一根由「向上流动的发光粒子尾迹」堆出来的光柱（没有实体柱面）。
##
## 门外是一片纯黑虚空，唯一能看见的就是这根粒子柱：走近它，白光会从画面中心（远处）
## 由远及近铺满屏幕，然后你就被送进门内世界（落点随机）。
## 单向：门内世界不再放门，进去就回不来。
##
## 结构：
##   Dust —— 大量自发光加色的粒子**尾迹**，从地板**下方 300 米**的一条扁槽向上飞；
##           粒子够多、尾迹够长，看起来就是一根柱。
##           发散角（emitter_spread_degrees）收得很紧，粒子几乎平行上飞，
##           整根柱从脚边到天顶都保持同一粗细，不会散成一片雾。
##   Gate —— 圆柱形感应区：玩家一靠近就发 gate_entered，由 main.gd 接管白闪与传送。
##
## 「向上无限延伸」怎么做到的：
##   光柱不封顶，一直伸到 pillar_height（默认 3000 米，比相机远裁剪面 1500 米还远），
##   所以从地面往上看，它永远是「插进天里、看不见头」。
##   粒子池大小固定 = Dust.amount（默认 3000）：池子一满，每生成一颗新粒子就顶掉
##   最老（也是最高）的那一颗 —— 先产生先回收（FIFO），数量恒 ≤ Dust.amount。
##   注意：GPUParticles3D 的发射速率 = Dust.amount ÷ 寿命，所以改 amount 就是改
##   「每秒生成多少颗」，同时也改了柱子里颗粒子的总量（两者本来就是同一个数）。
##
## 亮度：L(h) = L_min + (L_max - L_min) * exp(-k * |h|)
##   · h = 0（地板）处最亮；|h| 越大越暗，而且是带弧度的非线性衰减
##   · h → ±∞ 时趋近 L_min（> 0），**永远不会跌到 0** → 柱顶一直是「淡淡的、没有断」
##   · 只跟 |h| 有关，正负对称
##   这条曲线在 _ready() 里烘成一条 Gradient 写进 process_material.color_ramp
##   （粒子基本匀速上飞，所以「寿命进度」和「高度」一一对应，横轴就是高度）。

signal gate_entered(body: Node3D)

@export_group("尺寸")
## 光柱往上伸到多高（米）。默认 3000 —— 比相机远裁剪面（1500 米）高得多，
## 于是柱顶永远落在视野之外，看上去就是无限长。
@export_range(10.0, 20000.0, 10.0) var pillar_height: float = 3000.0
## 粒子柱的半径：发射槽的半宽 = 它 × emitter_width_ratio，柱子的粗细由这两者共同决定。
@export var pillar_radius: float = 2.2
## 发射点的高度（米，相对地板）：粒子在这个高度「出生」，然后往上飞。
## 现在是 −300：埋在地板以下 300 米，粒子从地下升上来、穿过地板再从脚边升起。
## 填正值（例如 10）就会改成悬在地板上方。
@export var emitter_offset_y: float = -300.0
## 上升速度（米/秒）。柱高由 pillar_height 锁死，所以调它只会变快，不会把柱子拉高拉稀。
@export_range(0.5, 60.0, 0.5) var rise_speed: float = 20.0
## 感应区半径：玩家水平距离小于它就会触发白闪。
@export_range(2.0, 40.0, 0.5) var gate_radius: float = 8.5

@export_group("粒子束")
## 粒子束的发散角（度）。0 = 所有粒子完全平行（最紧、最像一根实心柱）。
## 这是「束宽」的第一号旋钮：粒子从地下 300 米爬到地板，要横飘
## |emitter_offset_y| × tan(这个角) —— 2 度就能飘出 10 米宽，默认收到 0.2 度。
@export_range(0.0, 10.0, 0.05) var emitter_spread_degrees: float = 0.2
## 发射槽的半宽 = pillar_radius × 这个比例（0.45 → 半宽约 1 米）。
## 它决定粒子「出生时」的粗细；再加上上面的横飘量，就是 y = 0 处的束宽。
@export_range(0.05, 1.0, 0.05) var emitter_width_ratio: float = 0.45

@export_group("亮度曲线")
## h = 0（地板）处的最大亮度。相对值 —— 只有它和 brightness_min 的比值有意义。
@export var brightness_max: float = 10000.0
## 亮度下限（> 0）：无限高处无限接近它、但永远达不到，所以光柱顶端永远不为 0。
@export var brightness_min: float = 1000.0
## 衰减快慢（每米）：k 越大，高度一涨亮度掉得越快。
@export_range(0.0001, 0.02, 0.0001) var brightness_decay: float = 0.003

## 亮度曲线烘进 Gradient 时的采样点数。
const RAMP_STEPS := 64

@onready var _dust: GPUParticles3D = $Dust
@onready var _gate: Area3D = $Gate
@onready var _gate_shape: CollisionShape3D = $Gate/Shape


func _ready() -> void:
	_setup_dust()
	_setup_gate()
	_gate.body_entered.connect(_on_gate_body_entered)


## 光柱本体：不摆任何网格，只摆一台粒子发射器 + 粒子尾迹。
## 发射器埋在地板**下**（emitter_offset_y），粒子穿过地板升空 —— 远处看就是“从地上冒出来的一根柱”。
## 三个量分开管，互不打架：
##   高度 = pillar_height（米）
##   速度 = rise_speed（米/秒）
##   密度 = Dust.amount（颗数，同时也是粒子池大小 → 「产生一个就把最老的删掉」的那个上限）
func _setup_dust() -> void:
	_dust.position = Vector3(0.0, emitter_offset_y, 0.0)
	var mat := _dust.process_material
	if not (mat is ParticleProcessMaterial):
		return
	var pm := mat as ParticleProcessMaterial
	pm.emission_box_extents = Vector3(pillar_radius * emitter_width_ratio, 1.2, pillar_radius * emitter_width_ratio)
	pm.spread = emitter_spread_degrees
	pm.initial_velocity_min = rise_speed * 0.35
	pm.initial_velocity_max = rise_speed

	# 解「位移 = v*t + 0.5*a*t*t = 需要爬升的高度」求寿命。
	# 死亡时间 = 柱顶高度：越过寿命的粒子被回收，空出的槽位立刻给新粒子 → FIFO。
	var accel: float = maxf(pm.gravity.y, 0.0)
	var rise := pillar_height - emitter_offset_y
	var life := rise / rise_speed
	if accel > 0.001:
		life = (-rise_speed + sqrt(rise_speed * rise_speed + 2.0 * accel * rise)) / accel
	life = clampf(life, 0.5, 1200.0)
	_dust.lifetime = life
	_dust.preprocess = life
	# 包围盒要把整根柱罩住，不然升到一半就被视锥裁掉了。
	_dust.visibility_aabb = _beam_aabb(life, accel)
	# 亮度按高度衰减（见文件头的公式），烘成一条坡道挂在 process_material 上。
	pm.color_ramp = _brightness_ramp(life, accel)
	# 改完 lifetime / preprocess 要重启一次，否则首帧不是“已经飞满”的样子。
	_dust.restart()


## 粒子的可见包围盒：从发射槽一直罩到柱顶（+ 一点余量）。
func _beam_aabb(life: float, accel: float) -> AABB:
	var top: float = emitter_offset_y + rise_speed * life + 0.5 * accel * life * life
	var bottom: float = emitter_offset_y - 4.0
	var half: float = pillar_radius * 2.0 + 12.0
	return AABB(Vector3(-half, bottom, -half), Vector3(half * 2.0, top - bottom + 8.0, half * 2.0))


## 把 L(h) 烘成一条坡道：横轴 = 粒子寿命进度 0→1（也就是高度 0→柱顶）。
## 颜色只有 RGB 变（乘上亮度系数），alpha 恒为 1。
func _brightness_ramp(life: float, accel: float) -> GradientTexture1D:
	var grad := Gradient.new()
	grad.set_color(0, _ramp_color(0.0, life, accel))
	grad.set_color(1, _ramp_color(1.0, life, accel))
	for i in range(1, RAMP_STEPS - 1):
		var f := float(i) / float(RAMP_STEPS - 1)
		grad.add_point(f, _ramp_color(f, life, accel))
	var tex := GradientTexture1D.new()
	tex.gradient = grad
	tex.width = RAMP_STEPS
	return tex


## 寿命进度 f → 高度 h → 亮度系数（0 < s ≤ 1）。
func _ramp_color(f: float, life: float, accel: float) -> Color:
	var t: float = f * life
	var h: float = emitter_offset_y + rise_speed * t + 0.5 * accel * t * t
	var s: float = clampf(_brightness(h) / maxf(brightness_max, 0.0001), 0.0, 1.0)
	return Color(s, s, s, 1.0)


## L(h) = L_min + (L_max - L_min) * e^(-k*|h|)
## 恒 > 0；h = 0 最大；|h| 越大越小（带弧度）；h → ±∞ 趋近 L_min；正负对称。
func _brightness(h: float) -> float:
	return brightness_min + (brightness_max - brightness_min) * exp(-brightness_decay * absf(h))


## 感应区跟 gate_radius 走：不要求玩家真的碰到粒子，远远走近就会触发。
func _setup_gate() -> void:
	var shape := CylinderShape3D.new()
	shape.radius = gate_radius
	shape.height = 8.0
	_gate_shape.shape = shape
	_gate_shape.position = Vector3(0.0, 4.0, 0.0)


func _on_gate_body_entered(body: Node3D) -> void:
	if body is CharacterBody3D:
		gate_entered.emit(body)

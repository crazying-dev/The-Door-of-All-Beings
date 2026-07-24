extends Node3D

var z = 0 # 计数器-帧数
var FPS = 0
var GameTime = 0.0

# Called when the node enters the scene tree for the first time.
func _ready() -> void:
	pass # Replace with function body.


# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	FPS =  Engine.get_frames_per_second()
	GameTime = Engine.get_main_loop().get_ticks_msec() / 1000.0
	z += 1

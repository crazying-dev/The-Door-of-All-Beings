extends Control

@onready var StartAnimationLoadProgressBar: ProgressBar = $StartAnimationLoadProgressBar
@onready var StartCreenWithTextDom: Label = $StartCreenWithText
@onready var StartCreenProjectDescription: Label = $StartCreenProjectDescription
@onready var StartAnimation: TextureRect = $StartAnimation
@onready var Background: ColorRect = $Background
@onready var User: Control = $User

signal StartLoadUser()

var z = 0 # 计数器-帧数
const StartAnimationZ = 95
const StartAnimationFatherPath = "res://icons/TDOB/"
var StartAnimationList = []

func ToolModulateATo0(Body):
	for i in range(1, 0, -0.001):
		Body.modulate.a = i
		await get_tree().process_frame

func ToolModulateATo1(Body):
	for i in range(0, 1, 0.001):
		Body.modulate.a = i
		await get_tree().process_frame

func loadStartAnimation() -> void:
	StartAnimationLoadProgressBar.min_value = 0
	StartAnimationLoadProgressBar.max_value = StartAnimationZ
	StartAnimationLoadProgressBar.value = 0
	for i in range(StartAnimationZ):
		i += 1
		var StartAnimationNowPath = StartAnimationFatherPath + str(i) + ".png"
		StartAnimationList.append(load(StartAnimationNowPath))
		await get_tree().process_frame
		await get_tree().process_frame
		await get_tree().process_frame
		StartAnimationLoadProgressBar.value = i

func ChangeStartCreenWithText():
	await get_tree().create_timer(1.0).timeout
	StartCreenWithTextDom.hide()
	
	StartCreenProjectDescription.show()
	StartCreenProjectDescription.horizontal_alignment = 1
	await get_tree().create_timer(2.0).timeout
	StartCreenProjectDescription.hide()

func RunStartAnimation():
	StartAnimationLoadProgressBar.hide()
	StartAnimation.show()
	for i in range(StartAnimationZ):
		StartAnimation.texture = StartAnimationList[i]
		await get_tree().process_frame
		await get_tree().process_frame
		await get_tree().process_frame
		await get_tree().process_frame
		await get_tree().process_frame
		await get_tree().process_frame
		await get_tree().process_frame
		await get_tree().process_frame
	StartAnimation.hide()

func loadingUser():
	User.show()
	emit_signal("StartLoadUser")

# Called when the node enters the scene tree for the first time.
func _ready() -> void:
	loadStartAnimation()
	await ChangeStartCreenWithText()
	await RunStartAnimation()
	await loadingUser()


# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	z += 1

extends Control

@onready var UserLoaded:Control = $UserLoaded
@onready var User: Control = $User
@onready var StartCreenProjectDescription:Label = $StartCreenProjectDescription


# Called when the node enters the scene tree for the first time.
func _ready() -> void:
	UserLoaded.hide()
	User.hide()
	StartCreenProjectDescription.hide()


# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	pass

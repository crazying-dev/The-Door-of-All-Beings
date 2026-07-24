extends Control

@onready var input: LineEdit = $UserNameInput

# Called when the node enters the scene tree for the first time.
func _ready() -> void:
	input.modulate.a = 0.0


# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	pass

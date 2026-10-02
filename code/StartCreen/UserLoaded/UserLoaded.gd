extends Control

@onready var waiteLabel: Label = $"../waite"
@onready var UserNameLabel: Label = $Panel/TextureRect/UserName
@onready var OKButton:Button = $Panel/TextureRect/OK
@onready var UNOKButton: Button = $Panel/TextureRect/nuOK

signal LoadUserError(_UserIsLoad)
signal LoadUserOK()

func Break():
	hide()
	emit_signal("LoadUserError", false)

func LoadOK():
	emit_signal("LoadUserOK")

# Called when the node enters the scene tree for the first time.
func _ready() -> void:
	OKButton.pressed.connect(LoadOK)
	UNOKButton.pressed.connect(Break)
	hide()

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	pass


func UserLoaded(UserIsLoad) -> void:
	var Userfile = FileAccess.open("user://load.bin", FileAccess.READ)
	if Userfile == null:
		Break()
	var UserfileTEXT = Userfile.get_as_text()
	var JsonBody = JSON.new()
	var err = JsonBody.parse(UserfileTEXT)
	if err != OK:
		Break()
	else:
		var UserName = JsonBody.data["UserName"]
		UserNameLabel.text = UserName
		var InstallationUniqueIDfile = FileAccess.open("user://InstallationUniqueID.bin", FileAccess.READ)
		if UserIsLoad:
			if web(InstallationUniqueIDfile.get_as_text(), JsonBody.data["UserID"], UserName):
				LoadOK()
			else:
				Break()
		else:
			show()


func web(InstallationUniqueID,UserID,UserName) -> bool:
	"""
	网络校验，暂时留空
	"""
	return true

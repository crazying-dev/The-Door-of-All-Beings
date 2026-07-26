extends Control

@onready var input: LineEdit = $UserNameInput
@onready var OKButton: Button = $OK
@onready var http: HTTPRequest = $UUID4

const API_URL = "http://yjlt.top/TheDoorOfBings/UUID4/"
var InstallationUniqueID = ""

# 异步函数，必须搭配 await 使用
func fetch() -> Variant:
	var err = http.request(API_URL)
	if err != OK:
		push_error("请求发起失败")
		return null

	# 暂停等待请求完成
	var response = await http.request_completed
	var result = response[0]
	var response_code = response[1]
	var headers = response[2]
	var body = response[3]
	
	# 网络错误
	if result != HTTPRequest.RESULT_SUCCESS:
		print("网络请求失败")
		return null
	# http状态错误
	if response_code < 200 or response_code >= 300:
		print("响应码错误：", response_code)
		return null

	var raw = body.get_string_from_utf8()
	var json_data = JSON.parse_string(raw)

	if json_data is Array and json_data.size() > 0:
		return json_data[0]
	return null

func LoadUser():
	print("Get signal With StartLoadUser")
	await get_tree().process_frame
	input.editable = true
	input.focus_mode = Control.FOCUS_ALL
	input.grab_focus()
	input.edit(false)

func GETUserName(text:String=input.text):
	var UserName = text
	print("UserName:",UserName)
	var file = FileAccess.open("user://load.bin", FileAccess.WRITE)
	if file:
		var ReadMeaaage = {"UserName": UserName, "UserID":await fetch()}
		print(ReadMeaaage)
		file.store_string(JSON.stringify(ReadMeaaage, "\t"))

func GETInstallationUniqueID():
	const InstallationUniqueIDpath = "user://InstallationUniqueID.bin"
	if FileAccess.file_exists(InstallationUniqueIDpath):
		print("InstallationUniqueID.bin is true")
		var InstallationUniqueIDFile = FileAccess.open(InstallationUniqueIDpath, FileAccess.READ)
		InstallationUniqueID = InstallationUniqueIDFile.get_as_text()
		InstallationUniqueIDFile.close()
	else:
		print("InstallationUniqueID.bin is false")
		var InstallationUniqueIDFile = FileAccess.open(InstallationUniqueIDpath, FileAccess.WRITE)
		InstallationUniqueID = await fetch()
		InstallationUniqueIDFile.store_string(InstallationUniqueID)
		InstallationUniqueIDFile.close()
	print("InstallationUniqueID is " + InstallationUniqueID)

func IFLoadedWillNext():
	const UserINFOFilePath = "user://load.bin"
	if FileAccess.file_exists(UserINFOFilePath):
		hide()
	else:
		input.modulate.a = 1.0
		var Father = get_parent()
		Father.StartLoadUser.connect(LoadUser)
		OKButton.pressed.connect(GETUserName)
		hide()


# Called when the node enters the scene tree for the first time.
func _ready() -> void:
	GETInstallationUniqueID()
	IFLoadedWillNext()

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	pass


func _on_user_name_input_text_submitted(new_text: String) -> void:
	pass # Replace with function body.

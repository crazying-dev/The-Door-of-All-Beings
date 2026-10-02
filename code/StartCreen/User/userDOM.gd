extends Control

@onready var input: LineEdit = $Background/UserNameInput
@onready var OKButton: Button = $Background/OK
@onready var http: HTTPRequest = $UUID4
@onready var waiteLabel: Label = $"../waite"

const API_URL = "http://yjlt.top/TheDoorOfBings/UUID4/"
var InstallationUniqueID = ""

signal EndLoadUser(UserIsLoaded)


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
	waiteLabel.text = "已获取用户名"
	var UserName = text
	print("UserName:",UserName)
	var file = FileAccess.open("user://load.bin", FileAccess.WRITE)
	waiteLabel.text = "正在请求唯一ID..."
	var UserID = await fetch() 
	if UserID == null:
		waiteLabel.text = "ID请求失败，正在重试..."
		await get_tree().create_timer(2.0).timeout
		GETUserName(text)
	waiteLabel.text = "ID已获得"
	if file:
		var ReadMeaaage = {"UserName": UserName, "UserID": UserID}
		print(ReadMeaaage)
		file.store_string(JSON.stringify(ReadMeaaage, "\t"))
	waiteLabel.text = "ID已写入文件"

func GETInstallationUniqueID():
	const InstallationUniqueIDpath = "user://InstallationUniqueID.bin"
	waiteLabel.text = "正在获取本次安装ID..."
	if FileAccess.file_exists(InstallationUniqueIDpath):
		waiteLabel.text = "ID文件存在，正在读取..."
		print("InstallationUniqueID.bin is true")
		var InstallationUniqueIDFile = FileAccess.open(InstallationUniqueIDpath, FileAccess.READ)
		InstallationUniqueID = InstallationUniqueIDFile.get_as_text()
		InstallationUniqueIDFile.close()
	else:
		waiteLabel.text = "ID文件不存在，正在请求网络..."
		print("InstallationUniqueID.bin is false")
		var InstallationUniqueIDFile = FileAccess.open(InstallationUniqueIDpath, FileAccess.WRITE)
		InstallationUniqueID = await fetch()
		if InstallationUniqueID == null:
			waiteLabel.text = "网络错误，正在重试..."
			await get_tree().create_timer(2.0).timeout
			await GETInstallationUniqueID()
		waiteLabel.text = "已得到安装ID，开始写入文件..."
		InstallationUniqueIDFile.store_string(InstallationUniqueID)
		InstallationUniqueIDFile.close()
		waiteLabel.text = "已写入本次安装ID"
	waiteLabel.text = "已获取本次安装ID"
	print("InstallationUniqueID is " + InstallationUniqueID)

var UserIsLoad = false

func StartLoadUser(_UserIsLoad:bool=UserIsLoad):
	if _UserIsLoad:
		hide()
		emit_signal("EndLoadUser", UserIsLoad)
	else:
		show()

func IFLoadedWillNext():
	const UserINFOFilePath = "user://load.bin"
	waiteLabel.text = "检测用户是否已登陆..."
	if FileAccess.file_exists(UserINFOFilePath):
		waiteLabel.text = "用户已登录"
		UserIsLoad = true
	else:
		waiteLabel.text = "用户未登录，等待用户输入名称"
		input.modulate.a = 1.0
		var Father = get_parent()
		Father.StartLoadUser.connect(LoadUser)
		OKButton.pressed.connect(GETUserName)
		hide()
		emit_signal("EndLoadUser", UserIsLoad)



# Called when the node enters the scene tree for the first time.
func _ready() -> void:
	hide()
	await GETInstallationUniqueID()
	IFLoadedWillNext()

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	pass

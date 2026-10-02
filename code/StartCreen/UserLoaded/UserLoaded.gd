extends Control
## 用户确认面板（Scenes/start/StartCreen.tscn 的 UserLoaded 节点）。
##
## 显示本机已绑定的账号，并按设备锁规则限制更换：
##   Yes / LoadOK()  -> 发出 LoadUserOK，由根节点切换到下一个场景
##   No  / Break()   -> 设备锁不允许换号，给出提示后重新显示面板
##
## 场景里已经连好的信号（StartCreen.tscn L2008-L2012，不可改动）：
##   EndLoadUser(bool) -> UserLoaded(bool)
##   LoadUserError(bool)                   预留（本版本不再发出）

const AccountStore = preload("res://code/StartCreen/account_store.gd")

@onready var waiteLabel: Label = $"../waite"
@onready var UserNameLabel: Label = $Panel/TextureRect/UserName
@onready var IDLabel: Label = $Panel/TextureRect/IDL
@onready var OKButton: Button = $Panel/TextureRect/OK
@onready var UNOKButton: Button = $Panel/TextureRect/nuOK

signal LoadUserError(_UserIsLoad)
signal LoadUserOK()


func _ready() -> void:
	OKButton.pressed.connect(LoadOK)
	UNOKButton.pressed.connect(Break)
	hide()


func LoadOK() -> void:
	emit_signal("LoadUserOK")


## 用户拒绝使用本机账号：设备锁不允许更换，提示后重新显示面板。
func Break() -> void:
	hide()
	waiteLabel.text = "本机已绑定该账号，不能更换登录。\n如需更换请联系作者。"
	await get_tree().create_timer(2.5).timeout
	show()


func UserLoaded(UserIsLoad) -> void:
	if not UserIsLoad:
		hide()
		return
	var account := AccountStore.read_account()
	if account.is_empty():
		waiteLabel.text = "本地账号信息已损坏，暂时无法显示"
		hide()
		return
	UserNameLabel.text = str(account["UserName"])
	IDLabel.text = "ID\n" + str(account["UserID"])
	waiteLabel.text = "用户已登录"
	show()
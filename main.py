import hashlib
import Run_exe
import window
import pyperclip
import os
import hmac
import tools

home_path = os.path.expanduser("~")
file_path = os.path.join(home_path, ".Cr", "game", "the-dooe-od-bings")
os.makedirs(file_path, exist_ok=True)

# 登陆状态标识
login = False

# 1.核心文件存在性校验和文件哈希校验 => FileHashChecker.exe
FileHashChecker_return = Run_exe.RunExe(os.path.join(__file__, "FileHashChecker.exe"), None)
if not FileHashChecker_return[0]:
	text = ""
	choices_button={"退出":(exit, [0])}
	if FileHashChecker_return[1] == "TimeOut":
		text = "执行检验可执行文件时超时"
	elif FileHashChecker_return[1] == "FileNotFoundError":
		text = "无法找到正确的检验可执行文件"
	elif "Error" in FileHashChecker_return[1]:
		text = "未知错误，请点击拷贝并将剪切板中的文字发送给开发者"
		choices_button["拷贝"] = (pyperclip.copy, ["执行检验exe时出错，报错信息为:\n"+FileHashChecker_return[1]])
	window.Selection_Dialog(title="检验文件完整性时出错", text=text, choices_button=choices_button)

# 2.启动所有侧车 => LaunchSidecar.exe
LaunchSidecar_return = Run_exe.RunExe(os.path.join(__file__, "LaunchSidecar.exe"), None)
if not LaunchSidecar_return[0]:
	window.Selection_Dialog(title="侧车执行失败", text=f"侧车执行失败,错误原因:\n{FileHashChecker_return[1]}", choices_button={"退出":(exit, [0]), "拷贝":(pyperclip.copy, ["执行检验exe时出错，报错信息为:\n"+FileHashChecker_return[1]])})

# 3.验证用户信息存在性(~/.Cr/game/the-door-of-bings/cookies)
cookies_path = os.path.join(file_path, "cookies")
os.makedirs(cookies_path, exist_ok=True)
if os.path.isfile(os.path.join(cookies_path, "cookies.txt")) and os.path.isfile(os.path.join(cookies_path, "hashlib.bin")):
	# 4.确认用户数据正确性
	# 4.1.确认用户数据是否经过篡改
	with open(os.path.join(cookies_path, "hashlib.bin"), "r") as f:
		cookies_hashlib = f.read().strip() # hashlib.sha256().hexdigest()对象
	
	with open(os.path.join(cookies_path, "cookies.txt"), "r") as f:
		cookies_file_hashlib = hashlib.sha256(f.read().encode("utf-8"))
		current_hash_hex = cookies_file_hashlib.hexdigest()
	
	if hmac.compare_digest(cookies_hashlib, current_hash_hex):
		login = True
	else:
		window.Selection_Dialog("您的用户信息貌似存在篡改", "您的用户信息貌似存在篡改,无法正常启动游戏", {"删除用户数据(可能需要重新注册)":(tools.delUserInfo, []), "退出":(exit, [0])})
	
		
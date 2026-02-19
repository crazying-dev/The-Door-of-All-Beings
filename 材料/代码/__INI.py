# 众生之门 - 罗小黑战记非官方同人开源项目
# 原作IP：寒木春华动画工作室 / MTJJ
# 本项目非官方授权，仅用于非商业学习交流
import configparser
import os


def read_ini_file(ini_path="./config.ini", section=None):
	"""
	读取INI配置文件

	Args:
		ini_path (str): INI文件路径
		section (str, optional): 指定要读取的节，None则返回所有节

	Returns:
		dict: 读取到的配置字典，格式如 {section: {key: value, ...}}
	"""
	# 检查文件是否存在
	if not os.path.exists(ini_path):
		raise FileNotFoundError(f"INI文件不存在：{ini_path}")
	
	# 创建配置解析器对象
	config = configparser.ConfigParser()
	# 读取INI文件（支持UTF-8编码，避免中文乱码）
	config.read(ini_path, encoding="utf-8")
	
	# 存储解析后的配置
	config_dict = {}
	
	# 确定要读取的节列表
	sections_to_read = [section] if section else config.sections()
	
	for sec in sections_to_read:
		if sec not in config.sections():
			raise ValueError(f"INI文件中不存在节：{sec}")
		
		# 读取当前节的所有键值对
		config_dict[sec] = {}
		for key, value in config.items(sec):
			# 自动转换常见类型（布尔值、整数、浮点数）
			# 处理布尔值
			if value.lower() in ("true", "false"):
				config_dict[sec][key] = value.lower() == "true"
			# 处理整数
			elif value.isdigit():
				config_dict[sec][key] = int(value)
			# 处理浮点数
			elif "." in value and all(part.isdigit() for part in value.split(".") if part):
				config_dict[sec][key] = float(value)
			# 其他保持字符串
			else:
				config_dict[sec][key] = value
	
	return config_dict


# ------------------- 测试示例 -------------------
if __name__ == "__main__":
	# 读取整个INI文件
	try:
		all_config = read_ini_file("config.ini")
		print("读取所有配置：")
		print(all_config)
		
		# 提取EXE配置参数（可直接用于上一轮的exe调用函数）
		exe_config = all_config["EXE_CONFIG"]
		exe_path = exe_config["exe_path"]
		# 构造exe参数字典（只提取需要的参数）
		exe_params = {
			"o": exe_config["output_file"],
			"mode": exe_config["mode"],
			"verbose": exe_config["verbose"],
			"timeout": exe_config["timeout"]
		}
		param_style = exe_config["param_style"]
		
		print("\n提取的exe参数：")
		print(f"exe路径: {exe_path}")
		print(f"参数风格: {param_style}")
		print(f"参数字典: {exe_params}")
	
	# 读取指定节
	# other_config = read_ini_file("config.ini", section="OTHER")
	# print("\n指定节配置：", other_config)
	
	except Exception as e:
		print(f"读取INI文件出错：{e}")
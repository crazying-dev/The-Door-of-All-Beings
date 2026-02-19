# 众生之门 - 罗小黑战记非官方同人开源项目
# 原作IP：寒木春华动画工作室 / MTJJ
# 本项目非官方授权，仅用于非商业学习交流
import subprocess
import sys
import chardet  # 需要先安装：pip install chardet


def run_exe_with_params(exe_path, params_dict, param_style="-"):
	"""
	运行带参数的exe程序（修复编码问题）

	Args:
		exe_path (str): exe文件的路径
		params_dict (dict): 参数字典，如 {'o': 'main.txt', 'name': 'test'}
		param_style (str): 参数前缀，可选 '--'（长参数）或 '-'（短参数）

	Returns:
		tuple: (返回码, 标准输出, 标准错误)
	"""
	# 验证参数前缀是否合法
	if param_style not in ("--", "-"):
		raise ValueError("param_style 只能是 '--' 或 '-'")
	
	# 将参数字典转换为命令行参数列表
	cmd_args = [exe_path]
	for key, value in params_dict.items():
		cmd_args.append(f"{param_style}{key}")
		if value is not None:
			cmd_args.append(str(value))
	
	try:
		# 执行exe程序，先捕获字节流（不指定encoding）
		result = subprocess.run(
			cmd_args,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=False,  # 关键：先以字节模式读取，避免直接解码
			creationflags=subprocess.CREATE_NO_WINDOW  # 可选：隐藏exe的命令行窗口
		)
		
		# 自动检测输出编码（优先适配Windows的gbk）
		def decode_output(byte_data):
			if not byte_data:
				return ""
			# 第一步：尝试自动检测编码
			detected = chardet.detect(byte_data)
			encoding = detected["encoding"] or "gbk"
			# 第二步：优先用gbk（Windows默认），失败则用ignore忽略错误
			try:
				return byte_data.decode(encoding)
			except:
				return byte_data.decode("gbk", errors="ignore")  # 兜底方案
		
		# 解码stdout和stderr
		stdout = decode_output(result.stdout)
		stderr = decode_output(result.stderr)
		
		return (result.returncode, stdout, stderr)
	
	except FileNotFoundError:
		return -1, "", f"错误：找不到exe文件 '{exe_path}'"
	except Exception as e:
		return -1, "", f"执行出错：{str(e)}"


# ------------------- 测试示例 -------------------
if __name__ == "__main__":
	exe_path = "log.exe"  # 替换成你的exe路径
	params = {"H": "error", "m":1}
	
	return_code, stdout, stderr = run_exe_with_params(exe_path, params, "-")
	print("返回码:", return_code)
	print("标准输出:", stdout)
	if stderr:
		print("标准错误:", stderr)
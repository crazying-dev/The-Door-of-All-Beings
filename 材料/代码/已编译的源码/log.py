# 众生之门 - 罗小黑战记非官方同人开源项目
# 原作IP：寒木春华动画工作室 / MTJJ
# 本项目非官方授权，仅用于非商业学习交流
import argparse
import os
import time

try:
	from file_path import log as log_path
except Exception:
	log_path = "./log"

parser = argparse.ArgumentParser()
list1 = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

def validate_head(head:str):
	"""验证端口号是否有效"""
	head = head.upper()
	if head not in list1:
		raise argparse.ArgumentTypeError("日志头必须是DEBUG, INFO, ERROR中的一个")
	return head

def validate_error_level(error_level):
	level = int(error_level)
	if level not in [1,2,3]:
		raise argparse.ArgumentTypeError("错误等级必须是1~3的整数")
	return level

parser.add_argument('-H', '--head',
	                    type=validate_head,
	                    required=True,
	                    help='标题头')

parser.add_argument('-m', '--message',
	                    type=str,
	                    required=True,
	                    help='日志内容')

parser.add_argument('-e', '--error_level',
	                    type=validate_error_level,
	                    required=False,
	                    help='日志内容')

def log(head: str, message, error_level=3):
	global list1
	# 核心修复1：把文件路径拼接、目录创建逻辑移到函数内，每次调用都校验
	# 1. 拼接当日日志文件路径（确保每次调用都是最新日期）
	log_dir = log_path
	log_filename = f"{time.strftime('%Y-%m-%d')}.log"
	log_file = os.path.join(log_dir, log_filename)
	
	# 2. 强制创建目录（无论是否存在，exist_ok=True 避免报错）
	try:
		os.makedirs(log_dir, exist_ok=True)
		print(f"目录校验成功：{log_dir}（不存在则已创建）")
	except Exception as e:
		print(f"目录创建失败！原因：{str(e)}")
		return  # 目录创建失败，后续无法写入，直接返回
	
	# 3. 处理日志级别逻辑（保留你原有的逻辑）
	if head.upper() == "ERROR":
		if error_level == 3:
			head = list1[4]
		elif error_level == 2:
			head = list1[3]
		elif error_level == 1:
			head = list1[2]
		else:
			head = list1[3]
	else:
		head = head.upper()
	
	# 4. 格式化日志内容
	message = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{head}] {message}\n"
	
	# 核心修复2：添加异常捕获 + 指定编码，确保文件写入成功
	try:
		# 指定 encoding="utf-8" 解决中文乱码/创建失败问题
		with open(log_file, "a", encoding="utf-8") as f:
			f.write(message)
	except Exception as e:
		# 打印具体失败原因，方便排查（比如权限不足、路径非法）
		print(f"CRITICAL：{str(e)}")
args = parser.parse_args()
log(head=args.head, message=args.message, error_level=args.error_level if args.error_level else 3)

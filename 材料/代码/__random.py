# 众生之门 - 罗小黑战记非官方同人开源项目
# 原作IP：寒木春华动画工作室 / MTJJ
# 本项目非官方授权，仅用于非商业学习交流
import random
import 种族附属
import run
import Errors
import __INI

def get_random_animal():
	"""
	从种族附属.zhongsheng_animal_races中随机挑选一个动物返回

	Returns:
		str: 随机选中的动物名称，如"老虎"、"麻雀"、"章鱼"等
	"""
	# 获取动物信息库数据
	run.log("INFO", message='Star the choice animal')
	animal_db = 种族附属.zhongsheng_animal_races if 种族附属.zhongsheng_animal_races else None
	if not animal_db:
		run.log(head="error",message=f"The database of animal list the path is {__INI.read_ini_file()['path']['animal']} , but now can`t read")
		raise Errors.DatabaseError(f"The database of animal list the path is {__INI.read_ini_file()['path']['animal']} , but now can`t read")
	# 用于存储所有动物名称的列表
	all_animals = []
	
	def traverse_data(data):
		"""递归遍历嵌套数据，收集所有动物名称"""
		# 如果是字典，继续递归遍历其值
		if isinstance(data, dict):
			for value in data.values():
				traverse_data(value)
		# 如果是列表，说明是动物名称列表，直接添加到总列表
		elif isinstance(data, list):
			all_animals.extend(data)
	
	# 执行递归遍历，收集所有动物
	traverse_data(animal_db)
	
	# 检查是否收集到动物（防止数据为空）
	if not all_animals:
		run.log(head="error",message=f"Can`t find any animal in the database of animal list the path is {__INI.read_ini_file()['path']['animal']}")
		raise ValueError(f"Can`t find any animal in the database of animal list the path is {__INI.read_ini_file()['path']['animal']}")
	end = random.choice(all_animals)
	run.log("INFO", message=f'The-choice-animal`s end is {end}')
	# 随机返回一个动物
	return end


# ------------------- 测试调用 -------------------
if __name__ == "__main__":
	# 调用函数获取随机动物
	random_animal = get_random_animal()
	print(f"随机选中的动物：{random_animal}")
	
	# 多次调用示例（验证随机性）
	print("\n多次随机结果：")
	for i in range(5):
		print(f"第{i + 1}次：{get_random_animal()}")

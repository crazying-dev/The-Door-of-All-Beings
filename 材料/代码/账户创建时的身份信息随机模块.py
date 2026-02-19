# 众生之门 - 罗小黑战记非官方同人开源项目
# 原作IP：寒木春华动画工作室 / MTJJ
# 本项目非官方授权，仅用于非商业学习交流
"""
账号初始的四大信息：账号属性、生物形态、初始数值、自定义项
"""
import uuid
import time

import 设备验证
import 本机IP获取
import 种族附属
import run
import __random

ip = str(本机IP获取.ip)

def __uuid4_to_pure_digits():
	try:
		# 生成uuid4对象
		uuid_obj = uuid.uuid4()
		# 转换为128位整数（UUID本质）
		uuid_int = int(uuid_obj)
		run.log(head='INFO', message=f'Get uuid for user to as id , the uuid is {uuid_int}')
		return uuid_int
	except Exception as e:
		run.log(head="ERROR", message=f"Happen the ERROR when get uuid for user to as id \n the ERROR is {e}")


def __id_to_pure_digits(uuid_int=__uuid4_to_pure_digits()):
	try:
		temp = ""
		for i in ip:
			i = ord(i)
			temp = temp + str(i)
		temp = temp[::-1]
		temp = int(temp[10::])
		uuid_int = str(uuid_int)[::-1]
		uuid_int = int((uuid_int*10)[::-2][10::])
		output = int(str(temp+uuid_int)[0:20:1])
		run.log(head='INFO', message=f'Get id for user as id , the id is {output}')
		return output
	except Exception as e:
		run.log(head="ERROR", message=f"Happen the ERROR when get id for user as id \n the ERROR is {e}")



def main(name,妖精=0, animal=None):
	种族= animal if animal else __random.get_random_animal()
	模板 = {
		"种族": 种族,        #生物形态
		"妖精": bool(妖精),         #生物形态
		"id": __id_to_pure_digits(),        #账号属性
		"ip": ip,        #账号属性
		"name":name,        #账号属性 #自定义项
		"time": int(time.time()),        #账号属性
		"HP": 种族附属.animal_stats[种族][0],        #初始数值
		"ATK": 种族附属.animal_stats[种族][1],        #初始数值
		"DEF": 0,        #初始数值
		"device": 设备验证.get_device_unique_identifiers(),        #账号属性
		"LV": 0,        #初始数值
		"MP": 0         #初始数值
	}
	return 模板

if __name__ == '__main__':
	print(main("alan"))
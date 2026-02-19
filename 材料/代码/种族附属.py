# 众生之门 - 罗小黑战记非官方同人开源项目
# 原作IP：寒木春华动画工作室 / MTJJ
# 本项目非官方授权，仅用于非商业学习交流
import json

import __INI
import run
import Errors

config = __INI.read_ini_file()

encoding='utf-8'
animal = config["path"]["animal"]
animal_info = config["path"]["animal_info"]

try:
	with open(animal, "r", encoding=encoding) as f:
		zhongsheng_animal_races = json.loads(f.read())
		run.log(head='INFO', message=f'To read the file ,the file`s path is {animal}')
except Exception as e:
	run.log(head='error', message=f'To read the file, but happen the ERROR\nthe file`s path is {animal}\nthe Error is {e}')
	raise Errors.ReadFileError(f'To read the file, but happen the ERROR\nthe file`s path is {animal}\nthe Error is {e}')

try:
	with open(animal_info, "r", encoding=encoding) as f:
		animal_stats = json.loads(f.read())
		run.log(head='INFO', message=f'To read the file ,the file is {animal_info}')
except Exception as e:
	run.log(head='error', message=f'To read the file, but happen the ERROR\nthe file`s path is {animal_info}\nthe Error is {e}')
	raise Errors.ReadFileError(f'To read the file, but happen the ERROR\nthe file`s path is {animal_info}\nthe Error is {e}')

if __name__ == '__main__':
	print(zhongsheng_animal_races)
	print("="*200)
	print(animal_stats)
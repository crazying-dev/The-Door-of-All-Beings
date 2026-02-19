# 众生之门 - 罗小黑战记非官方同人开源项目
# 原作IP：寒木春华动画工作室 / MTJJ
# 本项目非官方授权，仅用于非商业学习交流
import __INI
import __run_exe


def log(head, message, error_level:int=3):
	path = __INI.read_ini_file()["path"]["log"]
	__run_exe.run_exe_with_params(path, {"m": message, "H": head, "e": error_level}, param_style="-")

if __name__ == '__main__':
	log('error', 1)
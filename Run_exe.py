import os
import subprocess
from typing import List, Any, Union


def RunExe(path:str, TimeOut: Union[int, float, None] =30, args:List=None, cwd=os.getcwd()) -> Any:
	if args is None:
		args = []
	try:
		ret = subprocess.run(
			[path]+args,
			shell=False,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			timeout=TimeOut,
			cwd=cwd
		)
		print("EXE返回码:", ret.returncode)
		print("stdout:\n", ret.stdout)
		print("stderr:\n", ret.stderr)
		
		if ret.returncode == 0:
			return True, ret.stdout
		else:
			return False, f"Error:\n\tstdout: {ret.stdout}\n\tstderr: {ret.stderr}"
	except subprocess.TimeoutExpired:
		return False, "TimeOut"
	except FileNotFoundError:
		return False, "FileNotFoundError"
	except Exception as e:
		return False, "Error with unknown\n"+str(e)

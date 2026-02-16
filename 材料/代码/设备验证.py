import hashlib
import platform
import uuid

import psutil


def get_device_unique_identifiers():
	"""
	返回设备唯一标识数组，同一设备返回值固定，不同设备不同（不受IP变化影响）
	返回格式：[核心硬件指纹, 系统标识哈希, 辅助硬件哈希]
	"""
	# 初始化存储原始硬件/系统信息的字典
	raw_info = {}
	system = platform.system()
	
	try:
		# -------------------------- 1. 核心硬件信息（跨平台） --------------------------
		# MAC地址（排除虚拟网卡/回环网卡）
		mac_addresses = []
		for iface, addrs in psutil.net_if_addrs().items():
			# 过滤虚拟/回环网卡
			if any(keyword in iface.lower() for keyword in ["virtual", "loopback", "lo", "vmware", "vbox"]):
				continue
			for addr in addrs:
				if addr.family == psutil.AF_LINK:
					mac = addr.address.replace("-", "").replace(":", "").strip()
					if mac and mac != "000000000000":
						mac_addresses.append(mac)
		raw_info["mac"] = "|".join(mac_addresses) if mac_addresses else str(uuid.getnode())
		
		# CPU信息（跨平台）
		if system == "Windows":
			import win32com.client
			wmi = win32com.client.GetObject("winmgmts:")
			for cpu in wmi.InstancesOf("Win32_Processor"):
				raw_info["cpu_id"] = cpu.ProcessorId.strip() if hasattr(cpu, "ProcessorId") else ""
				break
		elif system == "Linux":
			# 读取CPU标识
			cpu_info = ""
			try:
				with open("/proc/cpuinfo", "r") as f:
					for line in f:
						if "processor" in line and line.strip() == "processor\t: 0":
							continue
						if "cpu id" in line or "serial" in line or "model name" in line:
							cpu_info += line.strip()
			except:
				pass
			raw_info["cpu_id"] = cpu_info
		elif system == "Darwin":  # Mac
			try:
				import subprocess
				output = subprocess.check_output(["sysctl", "-n", "machdep.cpu.core_count", "machdep.cpu.model"],
				                                 stderr=subprocess.DEVNULL)
				raw_info["cpu_id"] = output.decode().strip()
			except:
				raw_info["cpu_id"] = ""
		
		# 硬盘/系统UUID（跨平台）
		if system == "Windows":
			# 获取系统盘卷标ID（管理员权限下稳定）
			try:
				import win32api
				raw_info["disk_id"] = win32api.GetVolumeInformation("C:\\")[1]
			except:
				raw_info["disk_id"] = ""
			# 获取Windows机器GUID（核心标识）
			try:
				import winreg
				key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
				raw_info["machine_guid"] = winreg.QueryValueEx(key, "MachineGuid")[0]
				winreg.CloseKey(key)
			except:
				raw_info["machine_guid"] = ""
		elif system == "Linux":
			# 读取系统UUID
			try:
				with open("/sys/class/dmi/id/product_uuid", "r") as f:
					raw_info["system_uuid"] = f.read().strip()
			except:
				raw_info["system_uuid"] = ""
		elif system == "Darwin":
			# Mac平台UUID
			try:
				import subprocess
				output = subprocess.check_output(["ioreg", "-d2", "-c", "IOPlatformExpertDevice"],
				                                 stderr=subprocess.DEVNULL)
				for line in output.decode().split("\n"):
					if "IOPlatformUUID" in line:
						raw_info["system_uuid"] = line.split("=")[1].strip().replace('"', '')
						break
			except:
				raw_info["system_uuid"] = ""
		
		# 主板/平台标识
		if system == "Windows":
			try:
				for board in wmi.InstancesOf("Win32_BaseBoard"):
					raw_info["board_id"] = board.SerialNumber.strip() if hasattr(board, "SerialNumber") else ""
					break
			except:
				raw_info["board_id"] = ""
		elif system == "Linux":
			try:
				with open("/sys/class/dmi/id/board_serial", "r") as f:
					raw_info["board_id"] = f.read().strip()
			except:
				raw_info["board_id"] = ""
		elif system == "Darwin":
			raw_info["board_id"] = platform.machine()
		
		# -------------------------- 2. 生成不可读的哈希标识 --------------------------
		# 核心硬件指纹（CPU+MAC+系统UUID/硬盘ID，优先级最高）
		core_hardware_str = "".join([
			raw_info.get("cpu_id", ""),
			raw_info.get("mac", ""),
			raw_info.get("machine_guid", ""),
			raw_info.get("system_uuid", ""),
			raw_info.get("disk_id", "")
		])
		core_fingerprint = hashlib.sha512(core_hardware_str.encode("utf-8")).hexdigest()
		
		# 系统标识哈希（系统版本+架构+主机名）
		system_str = "".join([
			platform.version(),
			platform.architecture()[0],
			platform.node(),
			system
		])
		system_fingerprint = hashlib.sha256(system_str.encode("utf-8")).hexdigest()
		
		# 辅助硬件哈希（主板+剩余硬件信息）
		aux_hardware_str = "".join([
			raw_info.get("board_id", ""),
			str(psutil.cpu_count(logical=True)),
			str(psutil.virtual_memory().total)
		])
		aux_fingerprint = hashlib.md5(aux_hardware_str.encode("utf-8")).hexdigest()
		
		# 返回标识数组（均为不可读的哈希字符串）
		return [core_fingerprint, system_fingerprint, aux_fingerprint]
	
	except Exception as e:
		# 极端异常情况下返回兜底唯一值（避免函数崩溃）
		fallback = hashlib.sha512(str(uuid.getnode()).encode()).hexdigest()
		return [fallback, fallback, fallback]


# 测试调用（管理员权限下执行）
if __name__ == "__main__":
	# 验证是否为管理员权限（Windows）
	if platform.system() == "Windows":
		import ctypes
		
		is_admin = ctypes.windll.shell32.IsUserAnAdmin()
		if not is_admin:
			print("非管理员")
	
	device_ids = get_device_unique_identifiers()
	print("数组：", device_ids)
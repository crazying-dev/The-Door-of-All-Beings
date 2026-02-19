# 众生之门 - 罗小黑战记非官方同人开源项目
# 原作IP：寒木春华动画工作室 / MTJJ
# 本项目非官方授权，仅用于非商业学习交流
import hashlib
import platform
import uuid
import psutil
import os
import run

def is_virtual_machine():
	"""检测是否为虚拟机环境"""
	vm_signatures = ["vmware", "virtualbox", "qemu", "kvm", "xen", "hyper-v", "vbox", "docker", "wsl"]
	system = platform.system()
	try:
		if system == "Windows":
			import win32com.client
			wmi = win32com.client.GetObject("winmgmts:")
			for comp in wmi.InstancesOf("Win32_ComputerSystem"):
				if comp.Model and any(sig in comp.Model.lower() for sig in vm_signatures):
					return True
		elif system == "Linux":
			if os.path.exists("/proc/cpuinfo"):
				with open("/proc/cpuinfo", "r") as f:
					content = f.read().lower()
					if any(sig in content for sig in vm_signatures):
						return True
		elif system == "Darwin":
			import subprocess
			output = subprocess.check_output(["system_profiler", "SPHardwareDataType"], stderr=subprocess.DEVNULL)
			if any(sig in output.decode().lower() for sig in vm_signatures):
				return True
	except Exception:
		pass
	return False


def generate_checksum(identifier_array):
	"""生成标识数组的校验位（防篡改）"""
	concat_str = "|".join(identifier_array)
	return hashlib.sha256(concat_str.encode()).hexdigest()[:4]


def get_device_unique_identifiers():
	"""
	返回设备唯一标识数组，同一设备返回值固定，不同设备不同（不受IP变化影响）
	返回格式：[核心硬件指纹, 系统标识哈希, 辅助硬件哈希, 校验位]
	"""
	run.log(head='info', message='Start to get DUID')
	raw_info = {}
	system = platform.system()
	is_vm = is_virtual_machine()  # 检测虚拟机
	
	try:
		# -------------------------- 1. 核心硬件信息（跨平台） --------------------------
		# MAC地址（排除虚拟/回环网卡）
		mac_addresses = []
		virtual_keywords = ["virtual", "loopback", "lo", "vmware", "vbox", "hyper-v", "docker", "wsl", "tap", "tun"]
		for iface, addrs in psutil.net_if_addrs().items():
			if any(keyword in iface.lower() for keyword in virtual_keywords):
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
			cpu_info = ""
			try:
				with open("/proc/cpuinfo", "r") as f:
					for line in f:
						line = line.strip()
						if line.startswith("processor") and line == "processor\t: 0":
							continue
						if line.startswith(("cpu id", "serial", "model name", "vendor_id")):
							cpu_info += line
			except:
				cpu_info = ""
			raw_info["cpu_id"] = cpu_info
		elif system == "Darwin":
			try:
				import subprocess
				output = subprocess.check_output(["sysctl", "-n", "machdep.cpu.core_count", "machdep.cpu.model"],
												 stderr=subprocess.DEVNULL)
				raw_info["cpu_id"] = output.decode().strip()
			except:
				raw_info["cpu_id"] = ""
		
		# 硬盘/系统UUID（跨平台）
		if system == "Windows":
			try:
				import win32api
				raw_info["disk_id"] = win32api.GetVolumeInformation("C:\\")[1]
			except:
				raw_info["disk_id"] = ""
			try:
				import winreg
				key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
				raw_info["machine_guid"] = winreg.QueryValueEx(key, "MachineGuid")[0]
				winreg.CloseKey(key)
			except:
				raw_info["machine_guid"] = ""
		elif system == "Linux":
			try:
				with open("/sys/class/dmi/id/product_uuid", "r") as f:
					raw_info["system_uuid"] = f.read().strip()
			except:
				raw_info["system_uuid"] = ""
		elif system == "Darwin":
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
		# 核心硬件指纹（用|分隔字段，避免空值碰撞）
		core_hardware_str = "|".join([
			raw_info.get("cpu_id", ""),
			raw_info.get("mac", ""),
			raw_info.get("machine_guid", ""),
			raw_info.get("system_uuid", ""),
			raw_info.get("disk_id", "")
		])
		core_fingerprint = hashlib.sha512(core_hardware_str.encode("utf-8")).hexdigest()
		
		# 系统标识哈希
		system_str = "|".join([
			platform.version(),
			platform.architecture()[0],
			platform.node(),
			system
		])
		system_fingerprint = hashlib.sha256(system_str.encode("utf-8")).hexdigest()
		
		# 辅助硬件哈希（替换MD5为SHA256）
		aux_hardware_str = "|".join([
			raw_info.get("board_id", ""),
			str(psutil.cpu_count(logical=True)),
			str(psutil.virtual_memory().total)
		])
		aux_fingerprint = hashlib.sha256(aux_hardware_str.encode("utf-8")).hexdigest()
		
		# 生成校验位
		base_array = [core_fingerprint, system_fingerprint, aux_fingerprint]
		checksum = generate_checksum(base_array)
		
		# 打印虚拟机提示（可选）
		if is_vm:
			print("警告：检测到虚拟机环境，标识唯一性可能受影响")
		
		# 返回标识数组（含校验位）
		return base_array + [checksum]
	
	except Exception as e:
		# 优化兜底逻辑
		fallback_str = "|".join([
			str(uuid.getnode()),
			platform.machine(),
			platform.system(),
			str(psutil.cpu_count(logical=True)),
			str(psutil.virtual_memory().total)
		])
		fallback = hashlib.sha512(fallback_str.encode()).hexdigest()
		fallback_checksum = generate_checksum([fallback, fallback, fallback])
		run.log(head='info', message='Get the DUID is right')
		return [fallback, fallback, fallback, fallback_checksum]

# 验证标识是否被篡改
def verify_identifier(identifier_array):
	run.log(head='info', message='Start Verification')
	if len(identifier_array) != 4:
		return False
	base_array = identifier_array[:3]
	actual_checksum = generate_checksum(base_array)
	end = not(identifier_array[3] == actual_checksum)
	run.log(head='info', message=f'The Verification is {end}')
	return end

# 测试调用（管理员权限下执行）
if __name__ == "__main__":
	# 验证是否为管理员权限（Windows）
	if platform.system() == "Windows":
		import ctypes
		
		is_admin = ctypes.windll.shell32.IsUserAnAdmin()
		if not is_admin:
			print("警告：非管理员权限，可能无法读取完整的硬件信息！")
	
	# 多次调用验证稳定性
	print("第一次调用结果：")
	device_ids1 = get_device_unique_identifiers()
	print(device_ids1)
	
	print("\n第二次调用结果：")
	device_ids2 = get_device_unique_identifiers()
	print(device_ids2)
	
	print(f"\n两次调用结果是否一致：{device_ids1 == device_ids2}")
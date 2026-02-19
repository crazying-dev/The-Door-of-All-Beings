# 众生之门 - 罗小黑战记非官方同人开源项目
# 原作IP：寒木春华动画工作室 / MTJJ
# 本项目非官方授权，仅用于非商业学习交流
import socket
import run

def get_public_local_ip():
	"""获取本机对外通信的IP地址"""
	run.log(head="INFO", message='Start to get local IP')
	try:
		# 创建一个UDP套接字（不会实际建立连接）
		s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		# 连接到一个公共的外部地址（这里用百度的IP，仅用于获取本机出口IP）
		s.connect(("www.baidu.com", 80))
		# 获取套接字绑定的本机IP
		local_ip = s.getsockname()[0]
		s.close()
		run.log(head="INFO", message=f'Get local IP is {local_ip}')
		return local_ip
	except Exception as e:
		run.log(head="ERROR", message=f'Can`t get local IP \nThe ERROR {e}', error_level=1)
		# 备用方案：获取本地回环地址
		run.log(head="INFO", message='Start to get local IP of two')
		try:
			local_ip = socket.gethostbyname(socket.gethostname())
			run.log(head="INFO", message=f'Get local IP is {local_ip}')
			return local_ip
		except Exception as e:
			run.log(head='error', message=f'Can`t get local IP of two\nThe ERROR {e}')

ip = get_public_local_ip()
# 调用函数并打印结果
if __name__ == "__main__":
	print(f"本机对外通信的IP地址：{ip}")
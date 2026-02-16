import socket

def get_public_local_ip():
    """获取本机对外通信的IP地址"""
    try:
        # 创建一个UDP套接字（不会实际建立连接）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 连接到一个公共的外部地址（这里用百度的IP，仅用于获取本机出口IP）
        s.connect(("www.baidu.com", 80))
        # 获取套接字绑定的本机IP
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        # 备用方案：获取本地回环地址
        return socket.gethostbyname(socket.gethostname())

ip = get_public_local_ip()
# 调用函数并打印结果
if __name__ == "__main__":
    print(f"本机对外通信的IP地址：{ip}")
from dotenv import load_dotenv
load_dotenv()

import flask
import os
import threading
import requests
import socket
import time
import hashlib
import hmac

import db
import game
import users
import actions
from device_id import DeviceId_can_use

# ---------------------------------------------------------------------------
# 套接字协议常量（协议详见 protocol.md）
# ---------------------------------------------------------------------------
PROTO_MAGIC    = "TheDoorOfBings"
PROTO_VERSIONS = {"1.0"}
ENCRYPT_METHODS = {"none"}
TIME_WINDOW_SEC = 300          # 请求时间戳允许偏差（防重放）

# 报错文本（与协议文档错误码表一致）
ERR_MAGIC      = "Invalid protocol magic header"
ERR_VERSION    = "Unsupported protocol version"
ERR_EXPIRED    = "Request expired"
ERR_ENCRYPT    = "Unsupported encryption algorithm"
ERR_PLAIN_HASH = "Invalid plain hash"
ERR_CIPHER_HASH = "Invalid cipher hash"
ERR_MALFORMED  = "Malformed packet structure"
ERR_DEVICE_ID  = "Invalid Device ID"
ERR_USER_ID    = "Invalid User ID"
ERR_ACTION     = "Unknown action"

# 动作注册表：action 名 -> handler(payload: str, device_id_hex: str, user_id: str) -> str
# handler 返回的字符串直接回写客户端；具体动作实现见 actions.py
ACTIONS: dict[str, callable] = actions.ACTIONS

def get_public_ip() -> str | None:
    try:
        # 稳定接口
        resp = requests.get("https://api.ipify.org", timeout=5)
        return resp.text.strip()
    except Exception as e:
        raise Exception(f"Error getting public IP: {e}")

IP = get_public_ip()

app = flask.Flask(__name__)
@app.route('/')
def index():
    return 'Welcome to the the door of all bings!'

@app.route('/game/api', methods=["POST"])
def game_api():
    return flask.jsonify({
        "port": os.getenv("PORT", 5423),
        "host": os.getenv("HOST", IP)
    })

class GameAPI:
    def __init__(self):
        """
        这里初始化配置,创建套接字,绑定地址和端口
        """
        self.can_run = False  # 服务启动标识
        if os.getenv("HOST", IP) == IP:
            self.can_run = True
            self.database = db.Database() # 创建数据库操作对象
            self.user_handler = users.user(self.database) # 账号业务（注册/登录）
            actions.init_handlers(self.user_handler)       # 注入给动作处理器

            # 启动 game 运行时数据定时落库（每分钟）
            game.start_autosave(self.database)

            # 创建 TCP 套接字并绑定
            self.port = int(os.getenv("PORT", 5423))
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("0.0.0.0", self.port))

    def run(self):
        """
        这里运行程序，启动监听
        """
        if not self.can_run:
            return

        self.sock.listen(16)
        print(f"[GameAPI] 监听 0.0.0.0:{self.port}")
        while True:
            try:
                conn, addr = self.sock.accept()
            except OSError:
                break  # 套接字已关闭，退出循环
            # 每个连接独立线程处理，避免阻塞接受新连接
            threading.Thread(
                target=self._handle_client, args=(conn, addr), daemon=True
            ).start()

    def _handle_client(self, conn: socket.socket, addr):
        """
        接收单个客户端的数据，交给 get_data 处理并回写响应
        """
        try:
            data = conn.recv(65536)
            if not data:
                return
            response = self.get_data(data, addr)
            if response:
                payload = response.encode() if isinstance(response, str) else response
                conn.sendall(payload)
        except Exception as e:
            print(f"[GameAPI] 处理客户端 {addr} 出错: {e}")
        finally:
            conn.close()

    def get_data(self, data: bytes, addr) -> str:
        """
        收到数据后进入这里处理（协议解析 + 动作分发，详见 protocol.md）

        合法数据格式:
        TheDoorOfBings | <version> | <UTCtime> | <encryption_method>    # 请求头
        <action> | <action_data>                                        # 正文
        [<device_id>] | [<userID>]                                      # 正文
        <plain_hash> | <cipher_hash>                                    # 请求尾
              |               |
        正文的明文的哈希   正文的密文哈希

        成功返回: OK | <action> | <handler结果>
        失败返回: 对应报错文本（见 ERR_* 常量）
        """
        # 解码
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ERR_MALFORMED

        # 按行拆分：请求头 / 正文两段 / 请求尾
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) != 4:
            return ERR_MALFORMED

        # 1. 请求头
        head = lines[0].split("|")
        if len(head) != 4 or head[0].strip() != PROTO_MAGIC:
            return ERR_MAGIC
        version = head[1].strip()
        if version not in PROTO_VERSIONS:
            return ERR_VERSION
        try:
            ts = int(head[2].strip())
        except ValueError:
            return ERR_MALFORMED
        if abs(time.time() - ts) > TIME_WINDOW_SEC:
            return ERR_EXPIRED
        if head[3].strip() not in ENCRYPT_METHODS:
            return ERR_ENCRYPT

        # 2. 正文两段
        body = [ln.split("|") for ln in lines[1:3]]
        if any(len(part) != 2 for part in body):
            return ERR_MALFORMED
        action, action_data = (p.strip() for p in body[0])
        device_id_hex, user_id = (p.strip() for p in body[1])

        # 3. 请求尾：明文哈希校验（对正文两行原文做 sha256）
        tail = lines[3].split("|")
        if len(tail) != 2:
            return ERR_MALFORMED
        plain_hash, cipher_hash = (p.strip() for p in tail)
        expected_plain = hashlib.sha256("\n".join(lines[1:3]).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(plain_hash, expected_plain):
            return ERR_PLAIN_HASH
        # 当前仅支持 none 加密，密文即明文；预留校验点
        if not hmac.compare_digest(cipher_hash, expected_plain):
            return ERR_CIPHER_HASH

        # 4. 设备ID合法性（hex -> bytes，密码学校验）
        try:
            device_id = bytes.fromhex(device_id_hex)
        except ValueError:
            return ERR_DEVICE_ID
        if device_id and not DeviceId_can_use(device_id):
            return ERR_DEVICE_ID

        # 5. 用户ID：已注册账号需与设备绑定
        if user_id and not self.user_handler.exists(user_id, device_id_hex):
            return ERR_USER_ID

        # 6. 动作分发
        handler = ACTIONS.get(action)
        if handler is None:
            return ERR_ACTION
        return handler(action_data, device_id_hex, user_id)

if __name__ == '__main__':
    threading.Thread(target=GameAPI().run, daemon=True).start()
    app.run(host="0.0.0.0", port=4231, debug=True)

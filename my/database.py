"""
兼容入口：原 database.py 已按职责拆分为三个模块，此处统一重导出，
保证 `import database` 的既有调用方式不变。

  device_id.py  设备ID生成与校验
  db.py         PostgreSQL 连接池(Database)
  users.py      用户账号操作(user)
"""

from db import Database
from device_id import DeviceId_can_use, DeviceId_get
from users import user

__all__ = ["Database", "DeviceId_get", "DeviceId_can_use", "user"]

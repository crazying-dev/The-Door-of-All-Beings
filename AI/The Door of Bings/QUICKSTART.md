# 快速启动指南

## 前置要求

### 客户端
- Unity Hub (推荐最新版本)
- Unity Editor 2022.3.21f1 或更高版本

### 服务器
- Node.js v18 或更高版本
- MySQL 8.0 或更高版本

## 安装步骤

### 1. 设置MySQL数据库

#### Windows:
1. 下载并安装 MySQL Community Server: https://dev.mysql.com/downloads/mysql/
2. 安装过程中记住root密码
3. 使用MySQL Workbench或命令行创建数据库:
   ```sql
   CREATE DATABASE the_door_of_bings CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```

### 2. 配置服务器

1. 打开命令行，进入服务器目录:
   ```bash
   cd "C:\Users\Alan_\Desktop\The Door of Bings\Server"
   ```

2. 安装依赖:
   ```bash
   npm install
   ```

3. 复制环境变量文件:
   ```bash
   copy .env.example .env
   ```

4. 编辑 `.env` 文件，设置以下内容:
   ```env
   DB_HOST=localhost
   DB_USER=root
   DB_PASSWORD=你的MySQL密码
   DB_NAME=the_door_of_bings
   JWT_SECRET=随意设置一个密钥
   PORT=7777
   LOG_LEVEL=info
   ```

5. 启动服务器:
   ```bash
   npm start
   ```

看到以下信息表示启动成功:
```
【众生之门】服务器已启动，端口: 7777
【众生之门】数据库初始化完成
```

### 3. 运行Unity客户端

1. 打开Unity Hub
2. 点击"添加"按钮，选择项目路径:
   ```
   C:\Users\Alan_\Desktop\The Door of Bings
   ```
3. 等待Unity加载项目
4. 创建一个测试场景:
   - 右键点击 `Assets/Scenes` 文件夹
   - 选择 `Create > Scene`
   - 命名为 `MainMenu`
5. 添加必要的GameObject:
   - 创建空GameObject，命名为 `GameManager`
   - 添加 `GameManager.cs` 脚本
   - 添加 `Unity.Netcode.NetworkManager` 组件
   - 添加 `PlayerManager.cs` 脚本
   - 添加 `DeviceBindingSystem.cs` 脚本
   - 添加 `DeathPenaltySystem.cs` 脚本
   - 添加 `CombatSystem.cs` 脚本
   - 添加 `DamageSystem.cs` 脚本
   - 添加 `AntiCheatSystem.cs` 脚本
   - 添加 `BehaviorAnalysis.cs` 脚本
6. 点击播放按钮运行游戏

## 测试流程

### 1. 测试服务器API

#### 注册账号:
```bash
curl -X POST http://localhost:7777/api/register ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"test1\",\"password\":\"password123\",\"deviceId\":\"device-001\"}"
```

#### 登录:
```bash
curl -X POST http://localhost:7777/api/login ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"test1\",\"password\":\"password123\",\"deviceId\":\"device-001\"}"
```

保存返回的 `token`，用于后续请求。

#### 创建角色:
```bash
curl -X POST http://localhost:7777/api/character/create ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer 你的token" ^
  -d "{\"characterName\":\"测试角色\",\"race\":\"Human\",\"identity\":\"Player\"}"
```

### 2. 测试游戏功能

1. 在Unity中运行游戏
2. 测试玩家移动
3. 测试战斗系统
4. 测试死亡惩罚
5. 测试设备绑定

## 常见问题

### Q: 服务器启动失败
A: 检查MySQL是否已启动，检查.env配置是否正确

### Q: Unity项目无法加载
A: 确保Unity版本是2022.3.21f1或更高版本

### Q: 数据库连接失败
A: 检查MySQL服务是否运行，检查用户名和密码是否正确

### Q: 设备绑定验证失败
A: 确保使用相同的设备ID，首次注册后设备ID无法更改

## 开发提示

1. **修改代码后**: 记得在Unity中保存场景和项目
2. **服务器重启**: 重启服务器会清空内存中的在线玩家列表
3. **数据库数据**: 数据会持久化存储，如需重置可删除数据库表
4. **日志查看**: 服务器日志保存在 `Server/logs/` 目录

## 下一步

- 查看 `README.md` 了解完整项目结构
- 查看 `rule.md` 了解游戏规则
- 开始开发游戏内容和UI界面

---

祝游戏开发顺利！
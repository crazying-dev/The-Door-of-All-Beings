# 众生之门 (The Door of Bings)

一个基于Unity 3D和Node.js的硬核MMORPG游戏，核心设计理念是**尊重现实生命概念**。

## 游戏特色

### 核心规则
- **设备绑定**：账号与首次激活的设备永久绑定，不可迁移
- **严格死亡惩罚**：角色死亡后档案注销，账号30分钟禁止登录，数据全部清零
- **红名系统**：人类击杀同势力人类触发红名，无法接取高阶任务
- **多身份系统**：普通玩家、BOSS、NPC、管理员四种身份
- **反作弊系统**：严格的设备验证和行为分析

### 技术栈

#### 客户端 (Unity 3D)
- **引擎**: Unity 2022.3.21f1
- **语言**: C#
- **网络**: Unity Netcode for GameObjects
- **渲染**: Universal Render Pipeline (URP)
- **UI**: Unity UI Toolkit

#### 服务器 (Node.js)
- **框架**: Express.js
- **实时通信**: Socket.io
- **数据库**: MySQL
- **认证**: JWT (JSON Web Token)
- **密码加密**: bcrypt

## 项目结构

```
The Door of Bings/
├── Assets/                      # Unity资源目录
│   ├── Scripts/                 # C#脚本
│   │   ├── Core/               # 核心系统
│   │   │   ├── GameManager.cs  # 游戏管理器
│   │   │   ├── Singleton.cs    # 单例基类
│   │   │   └── Enums.cs        # 枚举定义
│   │   ├── Player/             # 玩家系统
│   │   │   ├── PlayerData.cs   # 玩家数据
│   │   │   ├── PlayerManager.cs # 玩家管理器
│   │   │   ├── PlayerController.cs # 玩家控制器
│   │   │   ├── DeviceBindingSystem.cs # 设备绑定
│   │   │   └── DeathPenaltySystem.cs # 死亡惩罚
│   │   ├── Combat/             # 战斗系统
│   │   │   ├── CombatSystem.cs # 战斗系统
│   │   │   └── DamageSystem.cs # 伤害系统
│   │   ├── Network/            # 网络系统
│   │   │   ├── NetworkManager.cs # 网络管理器
│   │   │   ├── PlayerNetworkObject.cs # 玩家网络对象
│   │   │   └── GameSyncManager.cs # 游戏同步
│   │   └── AntiCheat/          # 反作弊系统
│   │       ├── AntiCheatSystem.cs # 反作弊系统
│   │       └── BehaviorAnalysis.cs # 行为分析
│   ├── Prefabs/                # 预制体
│   ├── Scenes/                 # 场景
│   └── Resources/              # 资源
├── ProjectSettings/            # Unity项目设置
├── Packages/                   # Unity包管理
└── Server/                     # 服务器代码
    ├── server.js              # 服务器主文件
    ├── package.json           # Node.js依赖
    └── .env.example           # 环境变量示例
```

## 快速开始

### 客户端 (Unity)

1. 打开Unity Hub
2. 添加项目: `C:\Users\Alan_\Desktop\The Door of Bings`
3. 等待Unity加载项目
4. 打开 `Assets/Scenes/MainMenu` 场景
5. 点击播放按钮运行

### 服务器 (Node.js)

1. 安装Node.js (推荐v18或更高版本)
2. 进入服务器目录:
   ```bash
   cd "C:\Users\Alan_\Desktop\The Door of Bings\Server"
   ```
3. 安装依赖:
   ```bash
   npm install
   ```
4. 配置环境变量:
   ```bash
   copy .env.example .env
   # 编辑.env文件，设置数据库连接信息
   ```
5. 启动服务器:
   ```bash
   npm start
   ```

服务器将在 `http://localhost:7777` 启动。

## 数据库配置

### 创建MySQL数据库

```sql
CREATE DATABASE the_door_of_bings CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 数据库表结构

服务器启动时会自动创建以下表：
- `accounts` - 账号表
- `characters` - 角色表
- `death_logs` - 死亡日志表
- `combat_logs` - 战斗日志表
- `ban_records` - 封禁记录表

## 游戏系统

### 玩家系统

- **种族**: 人类、妖精
- **身份**: 普通玩家、BOSS、NPC、管理员
- **势力**: 正义、邪恶、无势力
- **状态**: 正常、濒死、红名、禁锢

### 战斗系统

- 自由战斗
- 红名机制
- 伤害计算
- 范围攻击

### 死亡惩罚

- 濒死状态: 10秒
- 死亡判定: 彻底死亡
- 禁锢期: 30分钟
- 数据清零: 等级、经验、装备全部清零

### 设备绑定

- 首次激活设备永久绑定
- 硬件指纹验证
- 模拟器检测
- 多开检测

### 反作弊系统

- 速度作弊检测
- 传送作弊检测
- 伤害作弊检测
- 行为模式分析
- 内存完整性检查

## API文档

### 玩家注册
```http
POST /api/register
Content-Type: application/json

{
  "username": "player1",
  "password": "password123",
  "deviceId": "unique-device-id"
}
```

### 玩家登录
```http
POST /api/login
Content-Type: application/json

{
  "username": "player1",
  "password": "password123",
  "deviceId": "unique-device-id"
}
```

### 创建角色
```http
POST /api/character/create
Authorization: Bearer <token>
Content-Type: application/json

{
  "characterName": "测试角色",
  "race": "Human",
  "identity": "Player"
}
```

### 获取角色信息
```http
GET /api/character/:id
Authorization: Bearer <token>
```

### 角色死亡
```http
POST /api/character/:id/death
Authorization: Bearer <token>
```

## Socket.io事件

### 客户端 → 服务器
- `joinGame` - 加入游戏
- `playerMove` - 玩家移动
- `playerAttack` - 玩家攻击

### 服务器 → 客户端
- `playerJoined` - 新玩家加入
- `onlinePlayers` - 在线玩家列表
- `playerMoved` - 玩家移动
- `playerAttacked` - 玩家攻击
- `error` - 错误信息

## 开发计划

- [ ] 完善UI界面
- [ ] 添加更多战斗技能
- [ ] 实现任务系统
- [ ] 添加装备系统
- [ ] 实现社交系统
- [ ] 优化网络同步
- [ ] 添加国漫风渲染效果
- [ ] 实现世界生成系统

## 注意事项

### 重要提醒

1. **设备绑定**：每个账号只能绑定一个设备，绑定后无法更改
2. **死亡惩罚**：角色死亡后数据会清零，请谨慎战斗
3. **红名后果**：红名玩家无法接取高阶任务
4. **反作弊**：严禁使用外挂、脚本，一经发现永久封禁

### 开发提示

- 修改代码后记得保存Unity项目
- 服务器重启会清空内存中的在线玩家列表
- 数据库数据持久化存储
- 建议使用版本控制(Git)管理代码

## 许可证

游戏全部内容版权归寒春木动画工作室所有。

## 联系方式

- 项目地址: `C:\Users\Alan_\Desktop\The Door of Bings`
- 服务器端口: 7777

---

**妖灵会馆 宣**
**规则版本：众生之门·精简版 2.2**
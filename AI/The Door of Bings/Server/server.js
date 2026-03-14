/**
 * 众生之门游戏服务器
 * 基于 Node.js + Express + Socket.io
 */

const express = require('express');
const http = require('http');
const socketIo = require('socket.io');
const mysql = require('mysql2/promise');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const winston = require('winston');

// 配置日志
const logger = winston.createLogger({
  level: 'info',
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.json()
  ),
  transports: [
    new winston.transports.File({ filename: 'logs/error.log', level: 'error' }),
    new winston.transports.File({ filename: 'logs/combined.log' }),
    new winston.transports.Console()
  ]
});

// 创建 Express 应用
const app = express();
const server = http.createServer(app);
const io = socketIo(server, {
  cors: {
    origin: "*",
    methods: ["GET", "POST"]
  }
});

// 数据库配置
const dbConfig = {
  host: process.env.DB_HOST || 'localhost',
  user: process.env.DB_USER || 'root',
  password: process.env.DB_PASSWORD || '',
  database: process.env.DB_NAME || 'the_door_of_bings',
  charset: 'utf8mb4'
};

// 创建数据库连接池
const pool = mysql.createPool(dbConfig);

// 中间件
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// CORS
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept, Authorization');
  next();
});

// API 路由

/**
 * 玩家注册
 */
app.post('/api/register', async (req, res) => {
  try {
    const { username, password, deviceId } = req.body;

    // 验证必填字段
    if (!username || !password || !deviceId) {
      return res.status(400).json({ success: false, message: '缺少必填字段' });
    }

    // 检查用户名是否已存在
    const [existingUsers] = await pool.query(
      'SELECT id FROM accounts WHERE username = ?',
      [username]
    );

    if (existingUsers.length > 0) {
      return res.status(400).json({ success: false, message: '用户名已存在' });
    }

    // 检查设备是否已绑定其他账号
    const [existingDevices] = await pool.query(
      'SELECT id FROM accounts WHERE device_id = ?',
      [deviceId]
    );

    if (existingDevices.length > 0) {
      return res.status(400).json({ success: false, message: '该设备已绑定其他账号' });
    }

    // 加密密码
    const hashedPassword = await bcrypt.hash(password, 10);

    // 创建账号
    const [result] = await pool.query(
      'INSERT INTO accounts (username, password, device_id, created_at) VALUES (?, ?, ?, NOW())',
      [username, hashedPassword, deviceId]
    );

    logger.info(`新账号注册: ${username}, 设备ID: ${deviceId}`);

    res.json({
      success: true,
      message: '注册成功',
      data: {
        accountId: result.insertId
      }
    });

  } catch (error) {
    logger.error('注册失败:', error);
    res.status(500).json({ success: false, message: '服务器错误' });
  }
});

/**
 * 玩家登录
 */
app.post('/api/login', async (req, res) => {
  try {
    const { username, password, deviceId } = req.body;

    // 验证必填字段
    if (!username || !password || !deviceId) {
      return res.status(400).json({ success: false, message: '缺少必填字段' });
    }

    // 查询账号
    const [accounts] = await pool.query(
      'SELECT * FROM accounts WHERE username = ?',
      [username]
    );

    if (accounts.length === 0) {
      return res.status(401).json({ success: false, message: '用户名或密码错误' });
    }

    const account = accounts[0];

    // 验证设备绑定
    if (account.device_id !== deviceId) {
      logger.warn(`设备绑定验证失败: 用户 ${username}, 预期设备 ${account.device_id}, 实际设备 ${deviceId}`);
      return res.status(403).json({ success: false, message: '设备验证失败，账号与设备不匹配' });
    }

    // 验证密码
    const isPasswordValid = await bcrypt.compare(password, account.password);

    if (!isPasswordValid) {
      return res.status(401).json({ success: false, message: '用户名或密码错误' });
    }

    // 检查是否被封禁
    if (account.is_banned) {
      return res.status(403).json({ success: false, message: '该账号已被封禁' });
    }

    // 生成 JWT Token
    const token = jwt.sign(
      { accountId: account.id, username: account.username },
      process.env.JWT_SECRET || 'your-secret-key',
      { expiresIn: '24h' }
    );

    // 更新最后登录时间
    await pool.query(
      'UPDATE accounts SET last_login = NOW() WHERE id = ?',
      [account.id]
    );

    logger.info(`用户登录: ${username}, 设备ID: ${deviceId}`);

    res.json({
      success: true,
      message: '登录成功',
      data: {
        token,
        accountId: account.id,
        username: account.username
      }
    });

  } catch (error) {
    logger.error('登录失败:', error);
    res.status(500).json({ success: false, message: '服务器错误' });
  }
});

/**
 * 创建角色
 */
app.post('/api/character/create', async (req, res) => {
  try {
    const token = req.headers.authorization?.replace('Bearer ', '');

    if (!token) {
      return res.status(401).json({ success: false, message: '未授权' });
    }

    // 验证 Token
    const decoded = jwt.verify(token, process.env.JWT_SECRET || 'your-secret-key');

    const { characterName, race, identity } = req.body;

    // 验证必填字段
    if (!characterName || !race) {
      return res.status(400).json({ success: false, message: '缺少必填字段' });
    }

    // 检查角色名是否已存在
    const [existingCharacters] = await pool.query(
      'SELECT id FROM characters WHERE name = ?',
      [characterName]
    );

    if (existingCharacters.length > 0) {
      return res.status(400).json({ success: false, message: '角色名已存在' });
    }

    // 检查账号是否已有角色（根据规则，只能有一个角色）
    const [existingAccountCharacters] = await pool.query(
      'SELECT id FROM characters WHERE account_id = ?',
      [decoded.accountId]
    );

    if (existingAccountCharacters.length > 0) {
      return res.status(400).json({ success: false, message: '账号已存在角色，只能拥有一个角色' });
    }

    // 创建角色
    const [result] = await pool.query(
      'INSERT INTO characters (account_id, name, race, identity, level, health, max_health, experience, created_at) VALUES (?, ?, ?, ?, 1, 100, 100, 0, NOW())',
      [decoded.accountId, characterName, race, identity || 'Player']
    );

    logger.info(`新角色创建: ${characterName}, 账号ID: ${decoded.accountId}, 种族: ${race}`);

    res.json({
      success: true,
      message: '角色创建成功',
      data: {
        characterId: result.insertId
      }
    });

  } catch (error) {
    logger.error('创建角色失败:', error);
    res.status(500).json({ success: false, message: '服务器错误' });
  }
});

/**
 * 获取角色信息
 */
app.get('/api/character/:id', async (req, res) => {
  try {
    const token = req.headers.authorization?.replace('Bearer ', '');

    if (!token) {
      return res.status(401).json({ success: false, message: '未授权' });
    }

    const decoded = jwt.verify(token, process.env.JWT_SECRET || 'your-secret-key');
    const characterId = req.params.id;

    // 查询角色信息
    const [characters] = await pool.query(
      'SELECT * FROM characters WHERE id = ? AND account_id = ?',
      [characterId, decoded.accountId]
    );

    if (characters.length === 0) {
      return res.status(404).json({ success: false, message: '角色不存在' });
    }

    const character = characters[0];

    // 检查是否在禁锢期
    if (character.is_banned) {
      const banEndTime = new Date(character.ban_end_time);
      const now = new Date();

      if (now < banEndTime) {
        const remainingTime = Math.floor((banEndTime - now) / 1000); // 秒
        return res.status(403).json({
          success: false,
          message: '角色处于禁锢期',
          data: {
            remainingTime,
            banEndTime
          }
        });
      } else {
        // 禁锢期结束，更新状态
        await pool.query(
          'UPDATE characters SET is_banned = 0, ban_end_time = NULL WHERE id = ?',
          [characterId]
        );
        character.is_banned = 0;
      }
    }

    res.json({
      success: true,
      data: character
    });

  } catch (error) {
    logger.error('获取角色信息失败:', error);
    res.status(500).json({ success: false, message: '服务器错误' });
  }
});

/**
 * 角色死亡
 */
app.post('/api/character/:id/death', async (req, res) => {
  try {
    const token = req.headers.authorization?.replace('Bearer ', '');

    if (!token) {
      return res.status(401).json({ success: false, message: '未授权' });
    }

    const decoded = jwt.verify(token, process.env.JWT_SECRET || 'your-secret-key');
    const characterId = req.params.id;

    // 验证角色归属
    const [characters] = await pool.query(
      'SELECT * FROM characters WHERE id = ? AND account_id = ?',
      [characterId, decoded.accountId]
    );

    if (characters.length === 0) {
      return res.status(404).json({ success: false, message: '角色不存在' });
    }

    const character = characters[0];

    // 设置禁锢状态（30分钟）
    const banEndTime = new Date(Date.now() + 30 * 60 * 1000); // 30分钟后

    await pool.query(
      'UPDATE characters SET is_banned = 1, ban_end_time = ? WHERE id = ?',
      [banEndTime, characterId]
    );

    // 记录死亡日志
    await pool.query(
      'INSERT INTO death_logs (character_id, character_name, race, identity, level, death_time) VALUES (?, ?, ?, ?, ?, NOW())',
      [characterId, character.name, character.race, character.identity, character.level]
    );

    // 注销角色数据（清空）
    await pool.query(
      'UPDATE characters SET level = 1, health = 100, max_health = 100, experience = 0 WHERE id = ?',
      [characterId]
    );

    logger.info(`角色死亡: ${character.name}, 角色ID: ${characterId}`);

    res.json({
      success: true,
      message: '角色已死亡，数据已清零，禁锢30分钟',
      data: {
        banEndTime
      }
    });

  } catch (error) {
    logger.error('角色死亡处理失败:', error);
    res.status(500).json({ success: false, message: '服务器错误' });
  }
});

/**
 * Socket.io 连接处理
 */
io.on('connection', (socket) => {
  logger.info(`客户端连接: ${socket.id}`);

  // 玩家加入游戏
  socket.on('joinGame', async (data) => {
    try {
      const { token, characterId } = data;

      // 验证 Token
      const decoded = jwt.verify(token, process.env.JWT_SECRET || 'your-secret-key');

      // 查询角色信息
      const [characters] = await pool.query(
        'SELECT * FROM characters WHERE id = ? AND account_id = ?',
        [characterId, decoded.accountId]
      );

      if (characters.length === 0) {
        socket.emit('error', { message: '角色不存在' });
        return;
      }

      const character = characters[0];

      // 加入角色房间
      socket.join(`character_${characterId}`);

      // 广播新玩家加入
      socket.broadcast.emit('playerJoined', {
        characterId,
        characterName: character.name,
        race: character.race,
        identity: character.identity
      });

      // 发送在线玩家列表
      const [onlinePlayers] = await pool.query(
        'SELECT id, name, race, identity, level FROM characters WHERE is_banned = 0'
      );

      socket.emit('onlinePlayers', onlinePlayers);

      logger.info(`玩家加入游戏: ${character.name}, SocketID: ${socket.id}`);

    } catch (error) {
      logger.error('加入游戏失败:', error);
      socket.emit('error', { message: '加入游戏失败' });
    }
  });

  // 玩家移动
  socket.on('playerMove', (data) => {
    // 广播玩家移动
    socket.broadcast.emit('playerMoved', data);
  });

  // 玩家攻击
  socket.on('playerAttack', async (data) => {
    try {
      const { attackerId, targetId, damage } = data;

      // 记录战斗日志
      await pool.query(
        'INSERT INTO combat_logs (attacker_id, target_id, damage, combat_time) VALUES (?, ?, ?, NOW())',
        [attackerId, targetId, damage]
      );

      // 广播攻击事件
      io.emit('playerAttacked', data);

    } catch (error) {
      logger.error('处理攻击失败:', error);
    }
  });

  // 玩家断开连接
  socket.on('disconnect', () => {
    logger.info(`客户端断开连接: ${socket.id}`);
  });
});

// 初始化数据库
async function initializeDatabase() {
  try {
    // 创建数据库
    await pool.query(`CREATE DATABASE IF NOT EXISTS ${dbConfig.database} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`);

    // 切换到目标数据库
    await pool.query(`USE ${dbConfig.database}`);

    // 创建账号表
    await pool.query(`
      CREATE TABLE IF NOT EXISTS accounts (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        password VARCHAR(255) NOT NULL,
        device_id VARCHAR(255) UNIQUE NOT NULL,
        is_banned BOOLEAN DEFAULT FALSE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_login DATETIME,
        INDEX idx_username (username),
        INDEX idx_device_id (device_id)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    `);

    // 创建角色表
    await pool.query(`
      CREATE TABLE IF NOT EXISTS characters (
        id INT AUTO_INCREMENT PRIMARY KEY,
        account_id INT NOT NULL,
        name VARCHAR(50) UNIQUE NOT NULL,
        race ENUM('Human', 'Spirit') NOT NULL,
        identity ENUM('Player', 'Boss', 'NPC', 'Admin') DEFAULT 'Player',
        faction ENUM('None', 'Justice', 'Evil') DEFAULT 'None',
        state ENUM('Normal', 'NearDeath', 'RedName', 'Banned') DEFAULT 'Normal',
        level INT DEFAULT 1,
        health FLOAT DEFAULT 100,
        max_health FLOAT DEFAULT 100,
        experience FLOAT DEFAULT 0,
        is_banned BOOLEAN DEFAULT FALSE,
        ban_end_time DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_login DATETIME,
        FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
        INDEX idx_account_id (account_id),
        INDEX idx_name (name),
        INDEX idx_race (race),
        INDEX idx_identity (identity)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    `);

    // 创建死亡日志表
    await pool.query(`
      CREATE TABLE IF NOT EXISTS death_logs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        character_id INT NOT NULL,
        character_name VARCHAR(50) NOT NULL,
        race ENUM('Human', 'Spirit') NOT NULL,
        identity ENUM('Player', 'Boss', 'NPC', 'Admin') NOT NULL,
        level INT NOT NULL,
        cause VARCHAR(255) DEFAULT '战斗死亡',
        death_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_character_id (character_id),
        INDEX idx_death_time (death_time)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    `);

    // 创建战斗日志表
    await pool.query(`
      CREATE TABLE IF NOT EXISTS combat_logs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        attacker_id INT NOT NULL,
        target_id INT NOT NULL,
        damage FLOAT NOT NULL,
        combat_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_attacker_id (attacker_id),
        INDEX idx_target_id (target_id),
        INDEX idx_combat_time (combat_time)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    `);

    // 创建封禁记录表
    await pool.query(`
      CREATE TABLE IF NOT EXISTS ban_records (
        id INT AUTO_INCREMENT PRIMARY KEY,
        account_id INT NOT NULL,
        character_id INT,
        ban_type ENUM('SpeedHack', 'FlyHack', 'TeleportHack', 'DamageHack', 'HealthHack', 'DataModification', 'MemoryHack', 'TimeCheats', 'MultiInstance', 'ExternalProgram', 'Emulator', 'ManualBan') NOT NULL,
        reason TEXT,
        ban_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        permanent BOOLEAN DEFAULT FALSE,
        FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
        INDEX idx_account_id (account_id),
        INDEX idx_ban_time (ban_time)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    `);

    logger.info('数据库初始化完成');

  } catch (error) {
    logger.error('数据库初始化失败:', error);
  }
}

// 启动服务器
const PORT = process.env.PORT || 7777;

async function startServer() {
  try {
    await initializeDatabase();

    server.listen(PORT, () => {
      logger.info(`众生之门服务器已启动，端口: ${PORT}`);
      logger.info(`API地址: http://localhost:${PORT}`);
      logger.info(`Socket.io地址: ws://localhost:${PORT}`);
    });

  } catch (error) {
    logger.error('服务器启动失败:', error);
    process.exit(1);
  }
}

// 优雅关闭
process.on('SIGTERM', () => {
  logger.info('收到SIGTERM信号，正在关闭服务器...');
  server.close(() => {
    logger.info('服务器已关闭');
    process.exit(0);
  });
});

process.on('SIGINT', () => {
  logger.info('收到SIGINT信号，正在关闭服务器...');
  server.close(() => {
    logger.info('服务器已关闭');
    process.exit(0);
  });
});

// 启动
startServer();
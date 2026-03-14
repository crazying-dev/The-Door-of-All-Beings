using System;
using System.IO;
using UnityEngine;
using TheDoorOfBings.Core;

namespace TheDoorOfBings.Player
{
    /// <summary>
    /// 死亡惩罚系统 - 实现30分钟禁锢和数据清零
    /// </summary>
    public class DeathPenaltySystem : MonoBehaviour
    {
        public static DeathPenaltySystem Instance { get; private set; }
        
        private string deathLogPath;
        private float deathBanDuration = 1800f; // 30分钟（秒）
        
        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
            DontDestroyOnLoad(gameObject);
            
            InitializeDeathPenaltySystem();
        }
        
        private void InitializeDeathPenaltySystem()
        {
            deathLogPath = Path.Combine(Application.persistentDataPath, "DeathLogs");
            
            if (!Directory.Exists(deathLogPath))
            {
                Directory.CreateDirectory(deathLogPath);
            }
            
            Debug.Log($"【众生之门】死亡惩罚系统初始化完成");
        }
        
        /// <summary>
        /// 应用死亡惩罚
        /// </summary>
        public void ApplyDeathPenalty(PlayerData player)
        {
            if (player == null) return;
            
            Debug.Log($"【众生之门】应用死亡惩罚给玩家: {player.playerName}");
            
            // 1. 记录死亡日志
            RecordDeathLog(player);
            
            // 2. 设置禁锢状态
            SetPlayerBanned(player);
            
            // 3. 清除玩家数据
            ClearPlayerData(player);
            
            // 4. 注销角色
            PlayerManager.Instance.DeleteCharacter(player.playerId);
            
            // 5. 强制退出到主界面
            ForceExitGame();
            
            Debug.Log($"【众生之门】死亡惩罚应用完成");
        }
        
        /// <summary>
        /// 记录死亡日志
        /// </summary>
        private void RecordDeathLog(PlayerData player)
        {
            DeathLog log = new DeathLog
            {
                playerId = player.playerId,
                playerName = player.playerName,
                deathTime = DateTime.Now,
                race = player.race,
                identity = player.identity,
                level = player.level,
                cause = "战斗死亡"
            };
            
            string fileName = $"{player.playerId}_{DateTime.Now:yyyyMMdd_HHmmss}.json";
            string filePath = Path.Combine(deathLogPath, fileName);
            string json = JsonUtility.ToJson(log, true);
            
            try
            {
                File.WriteAllText(filePath, json);
                Debug.Log($"【众生之门】死亡日志已记录: {fileName}");
            }
            catch (Exception e)
            {
                Debug.LogError($"【众生之门】记录死亡日志失败: {e.Message}");
            }
        }
        
        /// <summary>
        /// 设置玩家禁锢状态
        /// </summary>
        private void SetPlayerBanned(PlayerData player)
        {
            DateTime banEndTime = DateTime.Now.AddSeconds(deathBanDuration);
            player.state = PlayerState.Banned;
            player.banEndTime = BitConverter.Int64BitsToDouble(banEndTime.ToBinary());
            
            Debug.Log($"【众生之门】玩家 {player.playerName} 已被禁锢，解除时间: {banEndTime:yyyy-MM-dd HH:mm:ss}");
        }
        
        /// <summary>
        /// 清除玩家数据
        /// </summary>
        private void ClearPlayerData(PlayerData player)
        {
            // 清除所有数据，只保留基本信息用于记录
            player.level = 1;
            player.experience = 0f;
            player.maxHealth = 100f;
            player.currentHealth = 100f;
            player.state = PlayerState.Banned;
            
            Debug.Log($"【众生之门】玩家 {player.playerName} 数据已清零");
        }
        
        /// <summary>
        /// 强制退出游戏
        /// </summary>
        private void ForceExitGame()
        {
            Debug.Log("【众生之门】角色死亡，强制退出游戏");
            
            // 延迟2秒后退出，给玩家时间看到死亡信息
            Invoke(nameof(ExitToMainMenu), 2f);
        }
        
        private void ExitToMainMenu()
        {
            // 加载主菜单场景
            UnityEngine.SceneManagement.SceneManager.LoadScene("MainMenu");
        }
        
        /// <summary>
        /// 检查玩家是否可以登录
        /// </summary>
        public bool CanPlayerLogin(PlayerData player)
        {
            if (player == null) return false;
            
            if (player.state != PlayerState.Banned)
            {
                return true;
            }
            
            // 检查禁锢期是否结束
            if (player.IsBanned())
            {
                float remainingTime = player.GetRemainingBanTime();
                Debug.LogWarning($"【众生之门】玩家 {player.playerName} 仍在禁锢期，剩余时间: {remainingTime}秒");
                return false;
            }
            
            // 禁锢期结束，可以创建新角色
            Debug.Log($"【众生之门】玩家 {player.playerName} 禁锢期已结束");
            return true;
        }
        
        /// <summary>
        /// 获取剩余禁锢时间
        /// </summary>
        public float GetRemainingBanTime(PlayerData player)
        {
            if (player == null || player.state != PlayerState.Banned)
            {
                return 0f;
            }
            
            return player.GetRemainingBanTime();
        }
    }
    
    /// <summary>
    /// 死亡日志
    /// </summary>
    [Serializable]
    public class DeathLog
    {
        public string playerId;
        public string playerName;
        public DateTime deathTime;
        public RaceType race;
        public IdentityType identity;
        public int level;
        public string cause;
    }
}
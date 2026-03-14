using System;
using System.IO;
using UnityEngine;
using TheDoorOfBings.Core;

namespace TheDoorOfBings.Player
{
    /// <summary>
    /// 玩家管理器 - 管理玩家账号、角色创建、数据存储
    /// </summary>
    public class PlayerManager : MonoBehaviour
    {
        public static PlayerManager Instance { get; private set; }
        
        private PlayerData currentPlayer;
        private string dataPath;
        private string accountId;
        private string deviceId;
        
        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
            DontDestroyOnLoad(gameObject);
            
            InitializePlayerManager();
        }
        
        private void InitializePlayerManager()
        {
            dataPath = Path.Combine(Application.persistentDataPath, "PlayerData");
            deviceId = SystemInfo.deviceUniqueIdentifier;
            
            Debug.Log($"【众生之门】玩家管理器初始化完成");
            Debug.Log($"【众生之门】设备ID: {deviceId}");
        }
        
        /// <summary>
        /// 创建新角色
        /// </summary>
        public PlayerData CreateNewCharacter(string accountId, string playerName, RaceType race, IdentityType identity = IdentityType.Player)
        {
            PlayerData newPlayer = new PlayerData
            {
                playerId = Guid.NewGuid().ToString(),
                playerName = playerName,
                deviceId = deviceId,
                race = race,
                identity = identity,
                faction = HumanFaction.None,
                state = PlayerState.Normal,
                createdTime = DateTime.Now,
                lastLoginTime = DateTime.Now
            };
            
            SavePlayerData(newPlayer);
            return newPlayer;
        }
        
        /// <summary>
        /// 加载玩家数据
        /// </summary>
        public PlayerData LoadPlayerData(string accountId)
        {
            string filePath = Path.Combine(dataPath, $"{accountId}.json");
            
            if (!File.Exists(filePath))
            {
                Debug.LogWarning($"【众生之门】玩家数据不存在: {accountId}");
                return null;
            }
            
            try
            {
                string json = File.ReadAllText(filePath);
                PlayerData playerData = JsonUtility.FromJson<PlayerData>(json);
                
                // 验证设备绑定
                if (playerData.deviceId != deviceId)
                {
                    Debug.LogError($"【众生之门】设备绑定验证失败！账号: {accountId}");
                    return null;
                }
                
                // 检查禁锢期
                if (playerData.IsBanned())
                {
                    float remainingTime = playerData.GetRemainingBanTime();
                    Debug.LogWarning($"【众生之门】账号处于禁锢期，剩余时间: {remainingTime}秒");
                    return null;
                }
                
                currentPlayer = playerData;
                currentPlayer.lastLoginTime = DateTime.Now;
                SavePlayerData(currentPlayer);
                
                return currentPlayer;
            }
            catch (Exception e)
            {
                Debug.LogError($"【众生之门】加载玩家数据失败: {e.Message}");
                return null;
            }
        }
        
        /// <summary>
        /// 保存玩家数据
        /// </summary>
        public void SavePlayerData(PlayerData player)
        {
            if (player == null) return;
            
            if (!Directory.Exists(dataPath))
            {
                Directory.CreateDirectory(dataPath);
            }
            
            string filePath = Path.Combine(dataPath, $"{player.playerId}.json");
            string json = JsonUtility.ToJson(player, true);
            
            try
            {
                File.WriteAllText(filePath, json);
                Debug.Log($"【众生之门】玩家数据已保存: {player.playerName}");
            }
            catch (Exception e)
            {
                Debug.LogError($"【众生之门】保存玩家数据失败: {e.Message}");
            }
        }
        
        /// <summary>
        /// 注销角色（死亡后调用）
        /// </summary>
        public void DeleteCharacter(string playerId)
        {
            string filePath = Path.Combine(dataPath, $"{playerId}.json");
            
            if (File.Exists(filePath))
            {
                try
                {
                    File.Delete(filePath);
                    Debug.Log($"【众生之门】角色已注销: {playerId}");
                }
                catch (Exception e)
                {
                    Debug.LogError($"【众生之门】注销角色失败: {e.Message}");
                }
            }
        }
        
        /// <summary>
        /// 获取当前玩家
        /// </summary>
        public PlayerData GetCurrentPlayer()
        {
            return currentPlayer;
        }
        
        /// <summary>
        /// 设置当前玩家
        /// </summary>
        public void SetCurrentPlayer(PlayerData player)
        {
            currentPlayer = player;
        }
        
        /// <summary>
        /// 检查账号是否存在
        /// </summary>
        public bool AccountExists(string accountId)
        {
            string filePath = Path.Combine(dataPath, $"{accountId}.json");
            return File.Exists(filePath);
        }
    }
}
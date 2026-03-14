using System;
using UnityEngine;
using TheDoorOfBings.Core;

namespace TheDoorOfBings.Player
{
    /// <summary>
    /// 玩家数据类 - 存储玩家所有信息
    /// </summary>
    [Serializable]
    public class PlayerData
    {
        // 基本信息
        public string playerId;
        public string playerName;
        public string deviceId; // 设备ID（用于设备绑定）
        
        // 种族和身份
        public RaceType race;
        public IdentityType identity;
        public HumanFaction faction;
        
        // 状态
        public PlayerState state;
        public float banEndTime; // 禁锢结束时间戳（Unix时间戳）
        
        // 生命值
        public float maxHealth = 100f;
        public float currentHealth = 100f;
        
        // 其他数据
        public int level = 1;
        public float experience = 0f;
        public DateTime createdTime;
        public DateTime lastLoginTime;
        
        public PlayerData()
        {
            createdTime = DateTime.Now;
            lastLoginTime = DateTime.Now;
            currentHealth = maxHealth;
        }
        
        /// <summary>
        /// 检查是否在禁锢期
        /// </summary>
        public bool IsBanned()
        {
            if (state != PlayerState.Banned) return false;
            return DateTime.Now < DateTime.FromBinary(BitConverter.DoubleToInt64Bits(banEndTime));
        }
        
        /// <summary>
        /// 获取剩余禁锢时间（秒）
        /// </summary>
        public float GetRemainingBanTime()
        {
            if (!IsBanned()) return 0f;
            DateTime endTime = DateTime.FromBinary(BitConverter.DoubleToInt64Bits(banEndTime));
            return (float)(endTime - DateTime.Now).TotalSeconds;
        }
    }
}
using System;
using System.Collections.Generic;
using UnityEngine;

namespace TheDoorOfBings.AntiCheat
{
    /// <summary>
    /// 行为分析系统 - 分析玩家行为模式
    /// </summary>
    public class BehaviorAnalysis : MonoBehaviour
    {
        public static BehaviorAnalysis Instance { get; private set; }
        
        [Header("行为分析配置")]
        [SerializeField] private float analysisInterval = 1f;
        [SerializeField] private int maxKillRate = 10; // 每分钟最大击杀数
        [SerializeField] private float maxResourceGatherRate = 100f; // 每分钟最大资源采集量
        
        private Dictionary<ulong, PlayerBehaviorData> playerBehaviors;
        private float lastAnalysisTime;
        
        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
            DontDestroyOnLoad(gameObject);
            
            InitializeBehaviorAnalysis();
        }
        
        private void InitializeBehaviorAnalysis()
        {
            playerBehaviors = new Dictionary<ulong, PlayerBehaviorData>();
            
            Debug.Log("【众生之门】行为分析系统初始化完成");
        }
        
        private void Update()
        {
            lastAnalysisTime += Time.deltaTime;
            
            if (lastAnalysisTime >= analysisInterval)
            {
                AnalyzeAllBehaviors();
                lastAnalysisTime = 0f;
            }
        }
        
        /// <summary>
        /// 分析所有玩家行为
        /// </summary>
        private void AnalyzeAllBehaviors()
        {
            foreach (var kvp in playerBehaviors)
            {
                AnalyzePlayerBehavior(kvp.Key);
            }
        }
        
        /// <summary>
        /// 分析玩家行为
        /// </summary>
        private void AnalyzePlayerBehavior(ulong playerId)
        {
            if (!playerBehaviors.ContainsKey(playerId))
            {
                return;
            }
            
            PlayerBehaviorData data = playerBehaviors[playerId];
            
            // 检查击杀率
            CheckKillRate(playerId, data);
            
            // 检查资源采集率
            CheckResourceGatherRate(playerId, data);
            
            // 检查异常行为
            CheckAbnormalBehavior(playerId, data);
        }
        
        /// <summary>
        /// 检查击杀率
        /// </summary>
        private void CheckKillRate(ulong playerId, PlayerBehaviorData data)
        {
            // 清理旧数据（超过1分钟）
            data.killHistory.RemoveAll(k => (DateTime.Now - k).TotalMinutes > 1);
            
            int killCount = data.killHistory.Count;
            
            if (killCount > maxKillRate)
            {
                Debug.LogWarning($"【众生之门】玩家 {playerId} 击杀率异常: {killCount}/分钟");
                AntiCheatSystem.Instance.BanPlayer(playerId, CheatType.DamageHack);
            }
        }
        
        /// <summary>
        /// 检查资源采集率
        /// </summary>
        private void CheckResourceGatherRate(ulong playerId, PlayerBehaviorData data)
        {
            // 清理旧数据（超过1分钟）
            data.resourceGatherHistory.RemoveAll(k => (DateTime.Now - k.Item2).TotalMinutes > 1);
            
            float totalResources = 0f;
            foreach (var item in data.resourceGatherHistory)
            {
                totalResources += item.Item1;
            }
            
            if (totalResources > maxResourceGatherRate)
            {
                Debug.LogWarning($"【众生之门】玩家 {playerId} 资源采集率异常: {totalResources}/分钟");
                AntiCheatSystem.Instance.BanPlayer(playerId, CheatType.DataModification);
            }
        }
        
        /// <summary>
        /// 检查异常行为
        /// </summary>
        private void CheckAbnormalBehavior(ulong playerId, PlayerBehaviorData data)
        {
            // 检查长时间挂机
            if (data.lastActionTime != DateTime.MinValue)
            {
                TimeSpan idleTime = DateTime.Now - data.lastActionTime;
                if (idleTime.TotalHours > 24)
                {
                    Debug.LogWarning($"【众生之门】玩家 {playerId} 长时间挂机: {idleTime.TotalHours}小时");
                }
            }
            
            // 检查机器人行为（重复操作）
            if (data.actionHistory.Count > 10)
            {
                // 检查最后10次操作是否完全相同
                bool isBotBehavior = CheckBotBehavior(data.actionHistory);
                
                if (isBotBehavior)
                {
                    Debug.LogWarning($"【众生之门】玩家 {playerId} 疑似使用脚本");
                    AntiCheatSystem.Instance.BanPlayer(playerId, CheatType.ExternalProgram);
                }
            }
        }
        
        /// <summary>
        /// 检查机器人行为
        /// </summary>
        private bool CheckBotBehavior(List<string> actionHistory)
        {
            if (actionHistory.Count < 10) return false;
            
            // 检查是否有10次完全相同的操作
            string lastAction = actionHistory[actionHistory.Count - 1];
            int sameActionCount = 0;
            
            for (int i = actionHistory.Count - 1; i >= 0 && i >= actionHistory.Count - 10; i--)
            {
                if (actionHistory[i] == lastAction)
                {
                    sameActionCount++;
                }
            }
            
            return sameActionCount >= 10;
        }
        
        /// <summary>
        /// 记录击杀
        /// </summary>
        public void RecordKill(ulong playerId)
        {
            if (!playerBehaviors.ContainsKey(playerId))
            {
                playerBehaviors[playerId] = new PlayerBehaviorData();
            }
            
            playerBehaviors[playerId].killHistory.Add(DateTime.Now);
            playerBehaviors[playerId].lastActionTime = DateTime.Now;
            playerBehaviors[playerId].actionHistory.Add("KILL");
            
            if (playerBehaviors[playerId].actionHistory.Count > 50)
            {
                playerBehaviors[playerId].actionHistory.RemoveAt(0);
            }
        }
        
        /// <summary>
        /// 记录资源采集
        /// </summary>
        public void RecordResourceGather(ulong playerId, float amount)
        {
            if (!playerBehaviors.ContainsKey(playerId))
            {
                playerBehaviors[playerId] = new PlayerBehaviorData();
            }
            
            playerBehaviors[playerId].resourceGatherHistory.Add(Tuple.Create(amount, DateTime.Now));
            playerBehaviors[playerId].lastActionTime = DateTime.Now;
            playerBehaviors[playerId].actionHistory.Add($"GATHER:{amount}");
            
            if (playerBehaviors[playerId].actionHistory.Count > 50)
            {
                playerBehaviors[playerId].actionHistory.RemoveAt(0);
            }
        }
        
        /// <summary>
        /// 记录玩家行为
        /// </summary>
        public void RecordAction(ulong playerId, string action)
        {
            if (!playerBehaviors.ContainsKey(playerId))
            {
                playerBehaviors[playerId] = new PlayerBehaviorData();
            }
            
            playerBehaviors[playerId].lastActionTime = DateTime.Now;
            playerBehaviors[playerId].actionHistory.Add(action);
            
            if (playerBehaviors[playerId].actionHistory.Count > 50)
            {
                playerBehaviors[playerId].actionHistory.RemoveAt(0);
            }
        }
    }
    
    /// <summary>
    /// 玩家行为数据
    /// </summary>
    public class PlayerBehaviorData
    {
        public List<DateTime> killHistory = new List<DateTime>();
        public List<Tuple<float, DateTime>> resourceGatherHistory = new List<Tuple<float, DateTime>>();
        public List<string> actionHistory = new List<string>();
        public DateTime lastActionTime = DateTime.MinValue;
        public Vector3 lastPosition;
        public DateTime lastPositionUpdateTime;
    }
}
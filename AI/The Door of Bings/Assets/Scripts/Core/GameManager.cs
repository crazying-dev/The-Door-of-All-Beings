using UnityEngine;
using TheDoorOfBings.Player;
using TheDoorOfBings.Combat;
using TheDoorOfBings.AntiCheat;

namespace TheDoorOfBings.Core
{
    /// <summary>
    /// 游戏管理器 - 核心单例
    /// </summary>
    public class GameManager : MonoBehaviour
    {
        public static GameManager Instance { get; private set; }

        [Header("游戏配置")]
        [SerializeField] private float deathBanDuration = 1800f; // 30分钟禁锢期（秒）

        public float DeathBanDuration => deathBanDuration;

        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }

            Instance = this;
            DontDestroyOnLoad(gameObject);

            InitializeGame();
        }

        private void InitializeGame()
        {
            Debug.Log("【众生之门】游戏初始化...");
            
            // 初始化各系统
            InitializePlayerSystem();
            InitializeCombatSystem();
            InitializeAntiCheatSystem();
            InitializeNetworkSystem();
            
            // 创建测试玩家数据
            CreateTestPlayer();
        }

        private void InitializePlayerSystem()
        {
            Debug.Log("【众生之门】玩家系统初始化完成");
        }

        private void InitializeCombatSystem()
        {
            Debug.Log("【众生之门】战斗系统初始化完成");
        }

        private void InitializeAntiCheatSystem()
        {
            Debug.Log("【众生之门】反作弊系统初始化完成");
        }

        private void InitializeNetworkSystem()
        {
            Debug.Log("【众生之门】网络系统初始化完成");
        }
        
        private void CreateTestPlayer()
        {
            // 创建测试玩家数据
            PlayerData testPlayer = new PlayerData
            {
                playerId = "test-player-001",
                playerName = "测试玩家",
                deviceId = SystemInfo.deviceUniqueIdentifier,
                race = RaceType.Human,
                identity = IdentityType.Player,
                faction = HumanFaction.None,
                state = PlayerState.Normal,
                level = 1,
                experience = 0f,
                maxHealth = 100f,
                currentHealth = 100f
            };
            
            PlayerManager.Instance.SetCurrentPlayer(testPlayer);
            
            Debug.Log($"【众生之门】测试玩家已创建: {testPlayer.playerName}");
            Debug.Log($"【众生之门】种族: {testPlayer.race}, 身份: {testPlayer.identity}");
        }

        private void OnApplicationQuit()
        {
            Debug.Log("【众生之门】游戏退出");
        }
    }
}
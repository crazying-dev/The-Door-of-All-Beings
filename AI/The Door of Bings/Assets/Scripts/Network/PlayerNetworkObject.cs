using Unity.Netcode;
using UnityEngine;
using TheDoorOfBings.Player;

namespace TheDoorOfBings.Network
{
    /// <summary>
    /// 玩家网络对象 - 同步玩家数据
    /// </summary>
    public class PlayerNetworkObject : NetworkBehaviour
    {
        [Header("玩家信息")]
        public NetworkVariable<string> playerName = new NetworkVariable<string>("");
        public NetworkVariable<int> level = new NetworkVariable<int>(1);
        public NetworkVariable<float> health = new NetworkVariable<float>(100f);
        public NetworkVariable<float> maxHealth = new NetworkVariable<float>(100f);
        public NetworkVariable<bool> isRedName = new NetworkVariable<bool>(false);
        
        private PlayerData playerData;
        private PlayerController playerController;
        
        public override void OnNetworkSpawn()
        {
            base.OnNetworkSpawn();
            
            playerController = GetComponent<PlayerController>();
            
            if (IsOwner)
            {
                InitializePlayerData();
            }
            
            // 注册值变化回调
            health.OnValueChanged += OnHealthChanged;
            isRedName.OnValueChanged += OnRedNameChanged;
        }
        
        public override void OnNetworkDespawn()
        {
            base.OnNetworkDespawn();
            
            health.OnValueChanged -= OnHealthChanged;
            isRedName.OnValueChanged -= OnRedNameChanged;
        }
        
        private void InitializePlayerData()
        {
            playerData = PlayerManager.Instance.GetCurrentPlayer();
            
            if (playerData != null)
            {
                playerName.Value = playerData.playerName;
                level.Value = playerData.level;
                health.Value = playerData.currentHealth;
                maxHealth.Value = playerData.maxHealth;
                isRedName.Value = playerData.state == TheDoorOfBings.Core.PlayerState.RedName;
            }
        }
        
        private void Update()
        {
            if (!IsOwner) return;
            
            // 同步生命值
            if (playerData != null)
            {
                if (health.Value != playerData.currentHealth)
                {
                    health.Value = playerData.currentHealth;
                }
                
                if (maxHealth.Value != playerData.maxHealth)
                {
                    maxHealth.Value = playerData.maxHealth;
                }
            }
        }
        
        /// <summary>
        /// 同步伤害
        /// </summary>
        [ServerRpc]
        public void TakeDamageServerRpc(float damage, ServerRpcParams rpcParams = default)
        {
            ulong senderId = rpcParams.Receive.SenderClientId;
            PlayerNetworkObject sender = GetPlayerNetworkObject(senderId);
            
            health.Value -= damage;
            
            if (health.Value <= 0)
            {
                health.Value = 0;
                OnPlayerDeathClientRpc();
            }
        }
        
        /// <summary>
        /// 客户端收到死亡通知
        /// </summary>
        [ClientRpc]
        private void OnPlayerDeathClientRpc()
        {
            if (IsOwner)
            {
                // 触发本地死亡逻辑
                if (playerController != null)
                {
                    // 这里需要调用PlayerController的死亡方法
                    // 由于是私有方法，可能需要重构为公共方法或通过事件触发
                }
            }
        }
        
        /// <summary>
        /// 同步治疗
        /// </summary>
        [ServerRpc]
        public void HealServerRpc(float amount)
        {
            health.Value += amount;
            if (health.Value > maxHealth.Value)
            {
                health.Value = maxHealth.Value;
            }
        }
        
        /// <summary>
        /// 同步红名状态
        /// </summary>
        [ServerRpc]
        public void SetRedNameServerRpc(bool redName)
        {
            isRedName.Value = redName;
        }
        
        private void OnHealthChanged(float oldValue, float newValue)
        {
            Debug.Log($"【众生之门】{playerName.Value} 生命变化: {oldValue} -> {newValue}");
        }
        
        private void OnRedNameChanged(bool oldValue, bool newValue)
        {
            Debug.Log($"【众生之门】{playerName.Value} 红名状态: {oldValue} -> {newValue}");
            
            // 更新UI显示
            UpdateRedNameVisual(newValue);
        }
        
        private void UpdateRedNameVisual(bool isRedName)
        {
            // TODO: 更新玩家外观以显示红名状态
            if (isRedName)
            {
                // 显示红色光效或标记
                Debug.Log($"【众生之门】{playerName.Value} 红名标记已显示");
            }
        }
        
        private PlayerNetworkObject GetPlayerNetworkObject(ulong clientId)
        {
            foreach (var player in FindObjectsOfType<PlayerNetworkObject>())
            {
                if (player.OwnerClientId == clientId)
                {
                    return player;
                }
            }
            return null;
        }
    }
}
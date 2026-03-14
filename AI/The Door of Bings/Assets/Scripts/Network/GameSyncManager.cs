using Unity.Netcode;
using UnityEngine;

namespace TheDoorOfBings.Network
{
    /// <summary>
    /// 游戏同步管理器 - 同步游戏状态
    /// </summary>
    public class GameSyncManager : NetworkBehaviour
    {
        public static GameSyncManager Instance { get; private set; }
        
        [Header("同步设置")]
        [SerializeField] private float syncInterval = 0.1f;
        
        private float lastSyncTime;
        
        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
        }
        
        private void Update()
        {
            if (!IsServer) return;
            
            lastSyncTime += Time.deltaTime;
            
            if (lastSyncTime >= syncInterval)
            {
                SyncGameState();
                lastSyncTime = 0f;
            }
        }
        
        /// <summary>
        /// 同步游戏状态
        /// </summary>
        private void SyncGameState()
        {
            // TODO: 同步所有玩家状态、世界状态等
        }
        
        /// <summary>
        /// 广播消息给所有客户端
        /// </summary>
        [ClientRpc]
        public void BroadcastMessageClientRpc(string message)
        {
            Debug.Log($"【众生之门】系统消息: {message}");
            
            // TODO: 显示在游戏UI上
        }
        
        /// <summary>
        /// 同步玩家位置
        /// </summary>
        public void SyncPlayerPosition(ulong clientId, Vector3 position)
        {
            if (!IsServer) return;
            
            SyncPlayerPositionClientRpc(clientId, position);
        }
        
        [ClientRpc]
        private void SyncPlayerPositionClientRpc(ulong clientId, Vector3 position)
        {
            PlayerNetworkObject player = GetPlayerNetworkObject(clientId);
            
            if (player != null && !player.IsOwner)
            {
                player.transform.position = position;
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
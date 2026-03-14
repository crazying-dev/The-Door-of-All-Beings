using Unity.Netcode;
using UnityEngine;
using TheDoorOfBings.Player;

namespace TheDoorOfBings.Network
{
    /// <summary>
    /// 网络管理器 - 使用Unity Netcode for GameObjects实现多人联机
    /// </summary>
    public class NetworkManager : MonoBehaviour
    {
        public static NetworkManager Instance { get; private set; }
        
        private Unity.Netcode.NetworkManager netManager;
        
        [Header("网络配置")]
        [SerializeField] private string serverIP = "127.0.0.1";
        [SerializeField] private ushort serverPort = 7777;
        
        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
            DontDestroyOnLoad(gameObject);
            
            InitializeNetwork();
        }
        
        private void InitializeNetwork()
        {
            netManager = GetComponent<Unity.Netcode.NetworkManager>();
            
            if (netManager == null)
            {
                netManager = gameObject.AddComponent<Unity.Netcode.NetworkManager>();
            }
            
            Debug.Log("【众生之门】网络管理器初始化完成");
        }
        
        /// <summary>
        /// 启动服务器
        /// </summary>
        public void StartServer()
        {
            if (netManager == null) return;
            
            netManager.StartServer();
            Debug.Log($"【众生之门】服务器已启动: {serverIP}:{serverPort}");
        }
        
        /// <summary>
        /// 启动主机
        /// </summary>
        public void StartHost()
        {
            if (netManager == null) return;
            
            netManager.StartHost();
            Debug.Log($"【众生之门】主机已启动: {serverIP}:{serverPort}");
        }
        
        /// <summary>
        /// 连接到服务器
        /// </summary>
        public void StartClient()
        {
            if (netManager == null) return;
            
            Unity.Netcode.Transports.NetworkTransport transport = netManager.NetworkConfig.NetworkTransport;
            
            if (transport is Unity.Netcode.Transports.UTP.UnityTransport utpTransport)
            {
                utpTransport.SetConnectionData(serverIP, serverPort);
            }
            
            netManager.StartClient();
            Debug.Log($"【众生之门】正在连接服务器: {serverIP}:{serverPort}");
        }
        
        /// <summary>
        /// 断开连接
        /// </summary>
        public void Disconnect()
        {
            if (netManager == null) return;
            
            if (netManager.IsHost)
            {
                netManager.Shutdown();
                Debug.Log("【众生之门】主机已关闭");
            }
            else if (netManager.IsClient)
            {
                netManager.Shutdown();
                Debug.Log("【众生之门】已断开服务器连接");
            }
        }
        
        /// <summary>
        /// 获取是否已连接
        /// </summary>
        public bool IsConnected()
        {
            return netManager != null && netManager.IsConnectedClient;
        }
        
        /// <summary>
        /// 获取是否为主机
        /// </summary>
        public bool IsHost()
        {
            return netManager != null && netManager.IsHost;
        }
        
        /// <summary>
        /// 获取当前客户端ID
        /// </summary>
        public ulong GetClientId()
        {
            return netManager != null ? netManager.LocalClientId : 0;
        }
    }
}
using System;
using System.IO;
using UnityEngine;
using TheDoorOfBings.Core;

namespace TheDoorOfBings.Player
{
    /// <summary>
    /// 设备绑定系统 - 确保账号与设备永久绑定
    /// </summary>
    public class DeviceBindingSystem : MonoBehaviour
    {
        public static DeviceBindingSystem Instance { get; private set; }
        
        private string deviceBindingPath;
        private string currentDeviceId;
        
        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
            DontDestroyOnLoad(gameObject);
            
            InitializeDeviceBinding();
        }
        
        private void InitializeDeviceBinding()
        {
            deviceBindingPath = Path.Combine(Application.persistentDataPath, "DeviceBinding");
            currentDeviceId = SystemInfo.deviceUniqueIdentifier;
            
            Debug.Log($"【众生之门】设备绑定系统初始化完成");
            Debug.Log($"【众生之门】当前设备ID: {currentDeviceId}");
        }
        
        /// <summary>
        /// 绑定设备到账号
        /// </summary>
        public bool BindDevice(string accountId)
        {
            if (IsDeviceBound(accountId))
            {
                Debug.LogWarning($"【众生之门】账号 {accountId} 已绑定设备");
                return VerifyDeviceBinding(accountId);
            }
            
            DeviceBindingInfo binding = new DeviceBindingInfo
            {
                accountId = accountId,
                deviceId = currentDeviceId,
                bindTime = DateTime.Now,
                macAddress = GetMacAddress(),
                cpuId = GetProcessorId(),
                motherBoardId = GetMotherboardId()
            };
            
            if (!Directory.Exists(deviceBindingPath))
            {
                Directory.CreateDirectory(deviceBindingPath);
            }
            
            string filePath = Path.Combine(deviceBindingPath, $"{accountId}.json");
            string json = JsonUtility.ToJson(binding, true);
            
            try
            {
                File.WriteAllText(filePath, json);
                Debug.Log($"【众生之门】账号 {accountId} 已绑定到设备");
                return true;
            }
            catch (Exception e)
            {
                Debug.LogError($"【众生之门】绑定设备失败: {e.Message}");
                return false;
            }
        }
        
        /// <summary>
        /// 验证设备绑定
        /// </summary>
        public bool VerifyDeviceBinding(string accountId)
        {
            string filePath = Path.Combine(deviceBindingPath, $"{accountId}.json");
            
            if (!File.Exists(filePath))
            {
                Debug.LogWarning($"【众生之门】账号 {accountId} 未绑定设备");
                return false;
            }
            
            try
            {
                string json = File.ReadAllText(filePath);
                DeviceBindingInfo binding = JsonUtility.FromJson<DeviceBindingInfo>(json);
                
                // 验证设备ID
                if (binding.deviceId != currentDeviceId)
                {
                    Debug.LogError($"【众生之门】设备验证失败！预期设备: {binding.deviceId}, 实际设备: {currentDeviceId}");
                    return false;
                }
                
                // 额外硬件验证（可选）
                if (!VerifyHardware(binding))
                {
                    Debug.LogWarning($"【众生之门】硬件验证异常，可能使用模拟器");
                    return false;
                }
                
                Debug.Log($"【众生之门】设备验证通过: {accountId}");
                return true;
            }
            catch (Exception e)
            {
                Debug.LogError($"【众生之门】验证设备绑定失败: {e.Message}");
                return false;
            }
        }
        
        /// <summary>
        /// 检查是否已绑定设备
        /// </summary>
        public bool IsDeviceBound(string accountId)
        {
            string filePath = Path.Combine(deviceBindingPath, $"{accountId}.json");
            return File.Exists(filePath);
        }
        
        /// <summary>
        /// 获取绑定信息
        /// </summary>
        public DeviceBindingInfo GetBindingInfo(string accountId)
        {
            string filePath = Path.Combine(deviceBindingPath, $"{accountId}.json");
            
            if (!File.Exists(filePath))
            {
                return null;
            }
            
            try
            {
                string json = File.ReadAllText(filePath);
                return JsonUtility.FromJson<DeviceBindingInfo>(json);
            }
            catch (Exception e)
            {
                Debug.LogError($"【众生之门】获取绑定信息失败: {e.Message}");
                return null;
            }
        }
        
        /// <summary>
        /// 验证硬件信息
        /// </summary>
        private bool VerifyHardware(DeviceBindingInfo binding)
        {
            // 简单的硬件验证
            string currentMac = GetMacAddress();
            string currentCpu = GetProcessorId();
            string currentMb = GetMotherboardId();
            
            // 允许一定的硬件变化（如升级），但不能完全不同
            int matchCount = 0;
            if (!string.IsNullOrEmpty(binding.macAddress) && binding.macAddress == currentMac) matchCount++;
            if (!string.IsNullOrEmpty(binding.cpuId) && binding.cpuId == currentCpu) matchCount++;
            if (!string.IsNullOrEmpty(binding.motherBoardId) && binding.motherBoardId == currentMb) matchCount++;
            
            // 至少匹配一个硬件信息
            return matchCount >= 1;
        }
        
        /// <summary>
        /// 获取MAC地址
        /// </summary>
        private string GetMacAddress()
        {
            try
            {
                // 简化的MAC地址获取
                return SystemInfo.deviceUniqueIdentifier.Substring(0, 8);
            }
            catch
            {
                return "UNKNOWN";
            }
        }
        
        /// <summary>
        /// 获取处理器ID
        /// </summary>
        private string GetProcessorId()
        {
            try
            {
                return SystemInfo.processorType;
            }
            catch
            {
                return "UNKNOWN";
            }
        }
        
        /// <summary>
        /// 获取主板ID
        /// </summary>
        private string GetMotherboardId()
        {
            try
            {
                return SystemInfo.graphicsDeviceName;
            }
            catch
            {
                return "UNKNOWN";
            }
        }
        
        /// <summary>
        /// 检测是否使用模拟器
        /// </summary>
        public bool IsEmulator()
        {
            // 简单的模拟器检测
            return SystemInfo.deviceModel.Contains("Emulator") ||
                   SystemInfo.deviceModel.Contains("Simulator") ||
                   Application.platform == RuntimePlatform.WindowsEditor ||
                   Application.platform == RuntimePlatform.OSXEditor;
        }
    }
    
    /// <summary>
    /// 设备绑定信息
    /// </summary>
    [Serializable]
    public class DeviceBindingInfo
    {
        public string accountId;
        public string deviceId;
        public DateTime bindTime;
        public string macAddress;
        public string cpuId;
        public string motherBoardId;
    }
}
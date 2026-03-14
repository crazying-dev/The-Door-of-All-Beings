using UnityEngine;
using TheDoorOfBings.Core;

namespace TheDoorOfBings.Player
{
    /// <summary>
    /// 玩家控制器 - 控制玩家行为
    /// </summary>
    [RequireComponent(typeof(CharacterController))]
    public class PlayerController : MonoBehaviour
    {
        [Header("玩家配置")]
        [SerializeField] private float moveSpeed = 5f;
        [SerializeField] private float jumpForce = 8f;
        [SerializeField] private float gravity = -9.81f;
        
        private CharacterController controller;
        private Vector3 velocity;
        private bool isGrounded;
        
        // 玩家数据引用
        private PlayerData playerData;
        
        private void Start()
        {
            controller = GetComponent<CharacterController>();
            playerData = PlayerManager.Instance.GetCurrentPlayer();
            
            if (playerData == null)
            {
                Debug.LogError("【众生之门】玩家数据未加载！");
                return;
            }
            
            Debug.Log($"【众生之门】玩家进入世界: {playerData.playerName}");
            Debug.Log($"【众生之门】种族: {playerData.race}, 身份: {playerData.identity}");
        }
        
        private void Update()
        {
            if (playerData == null) return;
            
            HandleMovement();
            HandleInteraction();
            UpdatePlayerState();
        }
        
        private void HandleMovement()
        {
            isGrounded = controller.isGrounded;
            
            if (isGrounded && velocity.y < 0)
            {
                velocity.y = -2f;
            }
            
            float x = Input.GetAxis("Horizontal");
            float z = Input.GetAxis("Vertical");
            
            Vector3 move = transform.right * x + transform.forward * z;
            controller.Move(move * moveSpeed * Time.deltaTime);
            
            if (Input.GetButtonDown("Jump") && isGrounded)
            {
                velocity.y = Mathf.Sqrt(jumpForce * -2f * gravity);
            }
            
            velocity.y += gravity * Time.deltaTime;
            controller.Move(velocity * Time.deltaTime);
        }
        
        private void HandleInteraction()
        {
            // NPC无法攻击玩家
            if (playerData.identity == IdentityType.NPC) return;
            
            // 管理员不进行实体交互
            if (playerData.identity == IdentityType.Admin) return;
        }
        
        private void UpdatePlayerState()
        {
            // 更新玩家状态逻辑
            if (playerData.state == PlayerState.RedName)
            {
                Debug.Log($"【众生之门】当前处于红名状态");
            }
        }
        
        /// <summary>
        /// 受到伤害
        /// </summary>
        public void TakeDamage(float damage)
        {
            if (playerData == null) return;
            
            // NPC身份无法被攻击
            if (playerData.identity == IdentityType.NPC)
            {
                Debug.Log("【众生之门】NPC身份受保护，无法被攻击");
                return;
            }
            
            playerData.currentHealth -= damage;
            
            if (playerData.currentHealth <= 0)
            {
                playerData.currentHealth = 0;
                EnterNearDeathState();
            }
            
            Debug.Log($"【众生之门】玩家受到伤害: {damage}, 剩余生命: {playerData.currentHealth}");
            SavePlayerData();
        }
        
        /// <summary>
        /// 进入濒死状态
        /// </summary>
        private void EnterNearDeathState()
        {
            playerData.state = PlayerState.NearDeath;
            Debug.Log("【众生之门】进入濒死状态，10秒后判定死亡");
            
            Invoke("OnPlayerDeath", 10f);
        }
        
        /// <summary>
        /// 玩家死亡
        /// </summary>
        private void OnPlayerDeath()
        {
            if (playerData == null) return;
            
            // 被救助则取消死亡
            if (playerData.currentHealth > 0)
            {
                CancelInvoke("OnPlayerDeath");
                return;
            }
            
            Debug.Log("【众生之门】玩家彻底死亡");
            DeathPenaltySystem.Instance.ApplyDeathPenalty(playerData);
        }
        
        /// <summary>
        /// 救助濒死玩家
        /// </summary>
        public void Revive(float healAmount)
        {
            if (playerData.state != PlayerState.NearDeath) return;
            
            playerData.currentHealth += healAmount;
            if (playerData.currentHealth > playerData.maxHealth)
            {
                playerData.currentHealth = playerData.maxHealth;
            }
            
            playerData.state = PlayerState.Normal;
            CancelInvoke("OnPlayerDeath");
            
            Debug.Log($"【众生之门】玩家被救助，恢复生命: {playerData.currentHealth}");
            SavePlayerData();
        }
        
        private void SavePlayerData()
        {
            PlayerManager.Instance.SavePlayerData(playerData);
        }
    }
}
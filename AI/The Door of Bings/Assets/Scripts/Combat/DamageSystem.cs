using UnityEngine;
using TheDoorOfBings.Player;

namespace TheDoorOfBings.Combat
{
    /// <summary>
    /// 伤害系统 - 计算伤害
    /// </summary>
    public class DamageSystem : MonoBehaviour
    {
        public static DamageSystem Instance { get; private set; }
        
        [Header("伤害配置")]
        [SerializeField] private float baseDamage = 10f;
        [SerializeField] private float criticalMultiplier = 1.5f;
        [SerializeField] private float criticalChance = 0.1f;
        
        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
        }
        
        /// <summary>
        /// 计算伤害
        /// </summary>
        public float CalculateDamage(PlayerData attacker, PlayerData defender, float baseDmg)
        {
            float damage = baseDmg;
            
            // 暴击判定
            if (Random.value < criticalChance)
            {
                damage *= criticalMultiplier;
                Debug.Log($"【众生之门】暴击！伤害: {damage}");
            }
            
            // TODO: 添加更多伤害计算逻辑（属性、装备、技能等）
            
            return damage;
        }
        
        /// <summary>
        /// 造成伤害
        /// </summary>
        public void DealDamage(GameObject target, float damage, GameObject attacker = null)
        {
            PlayerController targetController = target.GetComponent<PlayerController>();
            
            if (targetController == null) return;
            
            targetController.TakeDamage(damage);
            
            if (attacker != null)
            {
                Debug.Log($"【众生之门】{attacker.name} 对 {target.name} 造成 {damage} 点伤害");
            }
            else
            {
                Debug.Log($"【众生之门】{target.name} 受到 {damage} 点伤害");
            }
        }
        
        /// <summary>
        /// 治疗目标
        /// </summary>
        public void Heal(GameObject target, float amount)
        {
            PlayerController targetController = target.GetComponent<PlayerController>();
            
            if (targetController == null) return;
            
            targetController.Revive(amount);
            
            Debug.Log($"【众生之门】{target.name} 恢复 {amount} 点生命");
        }
        
        /// <summary>
        /// 造成范围伤害
        /// </summary>
        public void DealAreaDamage(Vector3 center, float radius, float damage, GameObject attacker = null)
        {
            Collider[] colliders = Physics.OverlapSphere(center, radius);
            
            foreach (Collider collider in colliders)
            {
                if (collider.CompareTag("Player"))
                {
                    DealDamage(collider.gameObject, damage, attacker);
                }
            }
            
            Debug.Log($"【众生之门】范围伤害: 中心 {center}, 半径 {radius}, 伤害 {damage}");
        }
        
        /// <summary>
        /// 治疗范围目标
        /// </summary>
        public void HealAreaTargets(Vector3 center, float radius, float amount)
        {
            Collider[] colliders = Physics.OverlapSphere(center, radius);
            
            foreach (Collider collider in colliders)
            {
                if (collider.CompareTag("Player"))
                {
                    Heal(collider.gameObject, amount);
                }
            }
            
            Debug.Log($"【众生之门】范围治疗: 中心 {center}, 半径 {radius}, 治疗 {amount}");
        }
    }
    
    /// <summary>
    /// 伤害类型
    /// </summary>
    public enum DamageType
    {
        Physical,    // 物理伤害
        Magic,       // 魔法伤害
        TrueDamage   // 真实伤害
    }
    
    /// <summary>
    /// 伤害结果
    /// </summary>
    public class DamageResult
    {
        public float damage;
        public bool isCritical;
        public bool isDodged;
        public bool isBlocked;
        public DamageType damageType;
        
        public DamageResult(float dmg, bool crit = false, bool dodged = false, bool blocked = false, DamageType type = DamageType.Physical)
        {
            damage = dmg;
            isCritical = crit;
            isDodged = dodged;
            isBlocked = blocked;
            damageType = type;
        }
    }
}
using UnityEngine;
using TheDoorOfBings.Core;
using TheDoorOfBings.Player;

namespace TheDoorOfBings.Combat
{
    /// <summary>
    /// 战斗系统 - 处理玩家间战斗和红名机制
    /// </summary>
    public class CombatSystem : MonoBehaviour
    {
        public static CombatSystem Instance { get; private set; }
        
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
        /// 攻击目标
        /// </summary>
        public void Attack(GameObject attacker, GameObject target, float damage)
        {
            if (attacker == null || target == null) return;
            
            PlayerController attackerController = attacker.GetComponent<PlayerController>();
            PlayerController targetController = target.GetComponent<PlayerController>();
            
            if (attackerController == null || targetController == null) return;
            
            PlayerData attackerData = PlayerManager.Instance.GetCurrentPlayer();
            PlayerData targetData = targetController.GetComponent<PlayerController>().GetType().GetField("playerData", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance).GetValue(targetController) as PlayerData;
            
            // 检查是否可以攻击
            if (!CanAttack(attackerData, targetData))
            {
                Debug.Log("【众生之门】无法攻击目标");
                return;
            }
            
            // 造成伤害
            targetController.TakeDamage(damage);
            
            // 检查是否触发红名
            CheckRedNameCondition(attackerData, targetData);
            
            Debug.Log($"【众生之门】{attackerData.playerName} 攻击了 {targetData.playerName}");
        }
        
        /// <summary>
        /// 检查是否可以攻击
        /// </summary>
        private bool CanAttack(PlayerData attacker, PlayerData target)
        {
            // 管理员不能进行实体交互
            if (attacker.identity == IdentityType.Admin)
            {
                Debug.Log("【众生之门】管理员身份不能进行实体交互");
                return false;
            }
            
            // NPC不能攻击玩家
            if (attacker.identity == IdentityType.NPC)
            {
                Debug.Log("【众生之门】NPC身份不能攻击玩家");
                return false;
            }
            
            // NPC身份受保护，不能被攻击
            if (target.identity == IdentityType.NPC)
            {
                Debug.Log("【众生之门】目标为NPC身份，受保护无法被攻击");
                return false;
            }
            
            // 管理员不可见，不能被攻击
            if (target.identity == IdentityType.Admin)
            {
                Debug.Log("【众生之门】目标为管理员身份，不可被攻击");
                return false;
            }
            
            return true;
        }
        
        /// <summary>
        /// 检查红名触发条件
        /// </summary>
        private void CheckRedNameCondition(PlayerData attacker, PlayerData target)
        {
            // 红名触发条件：人类击杀同势力人类
            if (attacker.race == RaceType.Human && 
                target.race == RaceType.Human && 
                attacker.faction != HumanFaction.None && 
                target.faction != HumanFaction.None && 
                attacker.faction == target.faction)
            {
                // 这里需要判断目标是否死亡
                // 由于攻击不一定会击杀，所以需要在目标死亡时再触发红名
                // 这里只标记潜在的冲突
                Debug.Log($"【众生之门】潜在红名冲突: {attacker.playerName} ({attacker.faction}) vs {target.playerName} ({target.faction})");
            }
        }
        
        /// <summary>
        /// 目标被击杀时调用
        /// </summary>
        public void OnTargetKilled(PlayerData killer, PlayerData victim)
        {
            if (killer == null || victim == null) return;
            
            // 检查是否触发红名
            if (ShouldTriggerRedName(killer, victim))
            {
                SetRedName(killer);
            }
            
            Debug.Log($"【众生之门】{killer.playerName} 击杀了 {victim.playerName}");
        }
        
        /// <summary>
        /// 判断是否应该触发红名
        /// </summary>
        private bool ShouldTriggerRedName(PlayerData killer, PlayerData victim)
        {
            // BOSS身份击杀任何玩家不触发红名
            if (killer.identity == IdentityType.Boss)
            {
                Debug.Log("【众生之门】BOSS身份击杀，不触发红名");
                return false;
            }
            
            // 不同种族互杀不触发红名
            if (killer.race != victim.race)
            {
                Debug.Log($"【众生之门】不同种族互杀，不触发红名");
                return false;
            }
            
            // 人类杀妖精不触发红名
            if (killer.race == RaceType.Human && victim.race == RaceType.Spirit)
            {
                Debug.Log("【众生之门】人类杀妖精，不触发红名");
                return false;
            }
            
            // 妖精杀任何玩家不触发红名
            if (killer.race == RaceType.Spirit)
            {
                Debug.Log("【众生之门】妖精击杀，不触发红名");
                return false;
            }
            
            // 人类击杀不同势力人类不触发红名
            if (killer.race == RaceType.Human && victim.race == RaceType.Human)
            {
                if (killer.faction == HumanFaction.None || victim.faction == HumanFaction.None)
                {
                    Debug.Log("【众生之门】无势力人类互杀，不触发红名");
                    return false;
                }
                
                if (killer.faction != victim.faction)
                {
                    Debug.Log($"【众生之门】不同势力人类互杀，不触发红名");
                    return false;
                }
            }
            
            // 人类击杀同势力人类，触发红名
            Debug.Log($"【众生之门】人类击杀同势力人类，触发红名！");
            return true;
        }
        
        /// <summary>
        /// 设置红名状态
        /// </summary>
        private void SetRedName(PlayerData player)
        {
            if (player.state == PlayerState.RedName)
            {
                Debug.Log($"【众生之门】玩家 {player.playerName} 已经是红名状态");
                return;
            }
            
            player.state = PlayerState.RedName;
            PlayerManager.Instance.SavePlayerData(player);
            
            Debug.LogWarning($"【众生之门】玩家 {player.playerName} 已成为红名！");
            
            // 通知所有玩家
            NotifyAllPlayersRedName(player);
        }
        
        /// <summary>
        /// 通知所有玩家某玩家成为红名
        /// </summary>
        private void NotifyAllPlayersRedName(PlayerData player)
        {
            // TODO: 实现网络通知
            Debug.Log($"【众生之门】全网通知: {player.playerName} 已成为红名，可被无限制攻击！");
        }
        
        /// <summary>
        /// 检查玩家是否为红名
        /// </summary>
        public bool IsRedName(PlayerData player)
        {
            return player != null && player.state == PlayerState.RedName;
        }
        
        /// <summary>
        /// 检查玩家是否可以接取高阶任务
        /// </summary>
        public bool CanAcceptHighLevelQuest(PlayerData player)
        {
            if (player == null) return false;
            
            if (IsRedName(player))
            {
                Debug.Log($"【众生之门】红名玩家 {player.playerName} 无法接取高阶任务");
                return false;
            }
            
            return true;
        }
    }
}
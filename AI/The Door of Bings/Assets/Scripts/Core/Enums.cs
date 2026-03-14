namespace TheDoorOfBings.Core
{
    /// <summary>
    /// 种族类型
    /// </summary>
    public enum RaceType
    {
        Human,      // 人类
        Spirit      // 妖精
    }

    /// <summary>
    /// 身份类型
    /// </summary>
    public enum IdentityType
    {
        Player,     // 普通玩家
        Boss,       // BOSS身份
        NPC,        // NPC身份
        Admin       // 管理员身份
    }

    /// <summary>
    /// 人类势力
    /// </summary>
    public enum HumanFaction
    {
        None,       // 无势力
        Justice,    // 正义势力
        Evil        // 邪恶势力
    }

    /// <summary>
    /// 玩家状态
    /// </summary>
    public enum PlayerState
    {
        Normal,     // 正常
        NearDeath,  // 濒死
        RedName,    // 红名
        Banned      // 禁锢中
    }
}
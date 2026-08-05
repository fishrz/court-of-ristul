"""位置识别回归测试。

背景：旧实现把 OpenDota 的 lane_role 当成 position 直接映射，但 lane_role
是「在哪条路」不是「打几号位」——一号位和五号位共用优势路，三号位和四号位
共用劣势路，于是每个阵营都会冒出两个「一号位」。这里用真实比赛数据锁死
正确行为。
"""

from app.poller import _assign_roles

# 真实比赛 8928901973（OpenDota version=22 已解析）。
# 用户实际反馈的错误样本：树精卫士(83)被判一号位、裂魂人(71)被判三号位。
MATCH_8928901973 = [
    {"player_slot": 0, "lane": 3, "lane_role": 3, "net_worth": 18403, "hero_id": 100},
    {"player_slot": 1, "lane": 3, "lane_role": 3, "net_worth": 14967, "hero_id": 105},
    {"player_slot": 2, "lane": 2, "lane_role": 2, "net_worth": 24982, "hero_id": 17},
    {"player_slot": 3, "lane": 1, "lane_role": 1, "net_worth": 14296, "hero_id": 21},
    {"player_slot": 4, "lane": 1, "lane_role": 1, "net_worth": 36051, "hero_id": 8},
    {"player_slot": 128, "lane": 3, "lane_role": 1, "net_worth": 29246, "hero_id": 10},
    {"player_slot": 129, "lane": 1, "lane_role": 3, "net_worth": 16133, "hero_id": 55},
    {"player_slot": 130, "lane": 2, "lane_role": 2, "net_worth": 20625, "hero_id": 90},
    {"player_slot": 131, "lane": 1, "lane_role": 3, "net_worth": 13347, "hero_id": 71},
    {"player_slot": 132, "lane": 3, "lane_role": 1, "net_worth": 12817, "hero_id": 83},
]

STANDARD = {"carry", "mid", "offlane", "soft_support", "hard_support"}


def test_real_match_positions_are_correct() -> None:
    roles = _assign_roles(MATCH_8928901973)

    # 天辉
    assert roles[4] == "carry"           # 主宰，36051 净资产
    assert roles[3] == "hard_support"    # 风行者，同优势路但经济只有 14296
    assert roles[2] == "mid"             # 风暴之灵
    assert roles[0] == "offlane"         # 巨牙海民
    assert roles[1] == "soft_support"    # 工程师

    # 夜魇 —— 用户明确指出的两个错误
    assert roles[132] == "hard_support"  # 树精卫士，绝不是一号位
    assert roles[131] == "soft_support"  # 裂魂人，是四号位不是三号位
    assert roles[128] == "carry"         # 变体精灵
    assert roles[129] == "offlane"       # 黑暗贤者
    assert roles[130] == "mid"           # 光之守卫


def test_no_duplicate_positions_within_a_team() -> None:
    """核心不变量：标准阵容里每个阵营的五个位置互不重复。

    旧实现在抽查的每一局、每一个阵营都违反了这条。
    """
    roles = _assign_roles(MATCH_8928901973)
    for radiant in (True, False):
        side = [
            roles[p["player_slot"]]
            for p in MATCH_8928901973
            if (p["player_slot"] < 128) is radiant
        ]
        assert sorted(side) == sorted(STANDARD)


def test_lane_role_alone_would_have_been_wrong() -> None:
    """守住这次修复的意义：单看 lane_role 一定分不出核心和辅助。"""
    lane_roles = [
        p["lane_role"] for p in MATCH_8928901973 if p["player_slot"] < 128
    ]
    assert lane_roles.count(1) == 2  # 主宰和风行者共用优势路
    assert lane_roles.count(3) == 2  # 巨牙海民和工程师共用劣势路


def test_missing_lane_data_yields_no_role_instead_of_a_guess() -> None:
    """拿不到 lane 就不给位置，让调用方兜底，不要编一个出来。"""
    players = [
        {"player_slot": 0, "lane": None, "lane_role": None, "net_worth": 20000},
        {"player_slot": 1, "lane": None, "lane_role": None, "net_worth": 5000},
    ]
    assert _assign_roles(players) == {}


def test_jungle_lane_is_not_forced_into_a_side_lane_slot() -> None:
    """lane 4 是野区/游走，没有同路对比基准，不参与排序。"""
    players = [
        {"player_slot": 0, "lane": 4, "lane_role": 4, "net_worth": 9000},
        {"player_slot": 1, "lane": 2, "lane_role": 2, "net_worth": 20000},
    ]
    roles = _assign_roles(players)
    assert 0 not in roles
    assert roles[1] == "mid"


def test_support_with_a_core_hero_is_still_a_support() -> None:
    """位置由资源分配决定，不由英雄决定。经济落后的就是辅助。"""
    players = [
        # 幻影刺客走优势路但经济被压制
        {"player_slot": 0, "lane": 1, "lane_role": 1, "net_worth": 6000},
        {"player_slot": 1, "lane": 1, "lane_role": 1, "net_worth": 25000},
    ]
    roles = _assign_roles(players)
    assert roles[0] == "hard_support"
    assert roles[1] == "carry"

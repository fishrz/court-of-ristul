"""L1 事实层 —— 从 OpenDota 原始解析包里挖出真正有诊断价值的字段。

背景：raw_json 每个玩家有 143 个字段，此前只用了 15 个左右（KDA/GPM/
补刀这些结果指标）。结果指标只能说「你打得差」，说不出「你哪一步开始
差的」。真正能给建议的是过程指标，它们一直躺在库里没人碰：

    benchmarks          全服同英雄百分位，队内对比永远给不出的参照系
    lh_t/gold_t/xp_t    逐分钟曲线，63 个点，能定位崩盘的那一分钟
    killed_by           被谁杀了几次 —— 死亡归因，比「你要少死」有用得多
    purchase_log        每件装备精确到手时间，配 meta 能算出早晚
    life_state_dead     总躺尸秒数，比 KDA 直观：损失了多少场上时间
    lane_efficiency_pct 对线效率
    purchase_tpscroll   全局买了几个 TP —— 非常具体的坏习惯

一条纪律：本模块只负责「取出事实」，不做判断。什么算晚、什么算差
一律交给 diagnose.py，因为那需要 meta 基准，而基准是会随版本变的。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 值得单独盯出装时间的核心装。刻意不列全部——
# 出个假腿早晚没人关心，BKB 早晚能决定一整局。
KEY_ITEMS = (
    "black_king_bar",
    "blink",
    "manta",
    "desolator",
    "radiance",
    "aghanims_scepter",
    "assault",
    "sheepstick",
    "silver_edge",
    "satanic",
    "abyssal_blade",
    "octarine_core",
    "force_staff",
    "glimmer_cape",
    "pipe",
    "crimson_guard",
)

# benchmarks 里我们真正会拿去说事的指标。OpenDota 还给了
# hero_healing_per_min 之类，对大多数英雄恒为 0 分位，是噪音。
BENCH_KEYS = (
    "gold_per_min",
    "xp_per_min",
    "last_hits_per_min",
    "hero_damage_per_min",
    "tower_damage",
    "kills_per_min",
)

BENCH_LABELS = {
    "gold_per_min": "经济",
    "xp_per_min": "经验",
    "last_hits_per_min": "补刀",
    "hero_damage_per_min": "输出",
    "tower_damage": "推塔",
    "kills_per_min": "击杀",
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def extract_benchmarks(raw_player: dict[str, Any]) -> dict[str, Any]:
    """全服同英雄百分位。

    这是整个诊断体系里性价比最高的一块：数据早就在 raw_json 里，
    但队内对比用不上它。有了它才能说出「你 GPM 打到同英雄前 3%，
    但推塔只有 60 分位——钱都变成装备摆着看了」这种话。
    赢的局也照样有话说，这点很重要。
    """
    src = raw_player.get("benchmarks")
    if not isinstance(src, dict):
        return {}
    out: dict[str, Any] = {}
    for key in BENCH_KEYS:
        item = src.get(key)
        if not isinstance(item, dict):
            continue
        pct, raw = _num(item.get("pct")), _num(item.get("raw"))
        if pct is None:
            continue
        out[key] = {
            "pct": round(pct * 100, 1),
            "raw": round(raw, 1) if raw is not None else None,
            "label": BENCH_LABELS.get(key, key),
        }
    return out


def extract_deaths(raw_player: dict[str, Any]) -> dict[str, Any]:
    """死亡归因 + 躺尸时间。

    killed_by 是被严重低估的字段。「你死于宙斯 4 次、黑暗贤者 4 次」
    直接指向「这局你死在法系爆发上」，再配上 BKB 时间就是一条
    可执行建议——不是「你要少死」，是「BKB 早出 6 分钟」。

    躺尸时间比死亡次数直观：后期一次死亡躺 60 秒 + 跑回线 30 秒，
    等于白送两波兵。用占比表达比绝对秒数更有冲击力。
    """
    dead = _num(raw_player.get("life_state_dead"))
    duration = _num(raw_player.get("duration"))
    killed_by = raw_player.get("killed_by")

    killers: list[dict[str, Any]] = []
    if isinstance(killed_by, dict):
        for hero_key, count in killed_by.items():
            n = _num(count)
            if not n:
                continue
            killers.append(
                {
                    # npc_dota_hero_zuus -> zuus，前端再映射中文名
                    "hero": str(hero_key).replace("npc_dota_hero_", ""),
                    "count": int(n),
                }
            )
        killers.sort(key=lambda x: x["count"], reverse=True)

    out: dict[str, Any] = {"killers": killers[:4]}
    if dead is not None and duration and duration > 0:
        out["dead_seconds"] = int(dead)
        out["dead_pct"] = round(dead / duration * 100, 1)
    return out


def extract_items(raw_player: dict[str, Any]) -> dict[str, Any]:
    """关键装备到手时间 + TP 习惯。

    purchase_log 里 time 是秒，可能为负（赛前购买）。只取正数时间的
    第一次购买——重复买的（如 BKB 被吞）不算第二次成型。
    """
    log = raw_player.get("purchase_log")
    timings: dict[str, int] = {}
    if isinstance(log, list):
        for entry in log:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key")
            time_s = _num(entry.get("time"))
            if not key or time_s is None or time_s < 0:
                continue
            name = str(key)
            if name in KEY_ITEMS and name not in timings:
                timings[name] = int(time_s)

    out: dict[str, Any] = {"timings": timings}
    tp = _num(raw_player.get("purchase_tpscroll"))
    if tp is not None:
        out["tp_bought"] = int(tp)
    return out


def extract_curve(raw_player: dict[str, Any]) -> dict[str, Any]:
    """逐分钟曲线 + 对线效率。

    lh_t 是累计正补的分钟数组（63 个点 = 62 分钟的局）。
    我们额外算出「每分钟增量」，因为崩盘表现为增量掉档，
    累计值看不出来——累计永远是单调上升的。
    """
    out: dict[str, Any] = {}
    lh_t = raw_player.get("lh_t")
    if isinstance(lh_t, list) and len(lh_t) >= 10:
        series = [int(v) for v in lh_t if isinstance(v, int | float)]
        out["lh_t"] = series
        out["lh_delta"] = [
            series[i] - series[i - 1] for i in range(1, len(series))
        ]

    for key in ("gold_t", "xp_t"):
        arr = raw_player.get(key)
        if isinstance(arr, list) and len(arr) >= 10:
            out[key] = [int(v) for v in arr if isinstance(v, int | float)]

    eff = _num(raw_player.get("lane_efficiency_pct"))
    if eff is not None:
        out["lane_efficiency_pct"] = int(eff)

    for key in ("rune_pickups", "camps_stacked", "buyback_count", "pings"):
        value = _num(raw_player.get(key))
        if value is not None:
            out[key] = int(value)
    return out


def extract_player_facts(raw_player: dict[str, Any]) -> dict[str, Any]:
    """把一名玩家的深度事实打包。存进 match_players.metrics_json。"""
    return {
        "benchmarks": extract_benchmarks(raw_player),
        "deaths": extract_deaths(raw_player),
        "items": extract_items(raw_player),
        "curve": extract_curve(raw_player),
        "rank_tier": raw_player.get("rank_tier"),
        "hero_id": raw_player.get("hero_id"),
    }


def enemy_hero_ids(raw_match: dict[str, Any], our_side: str | None) -> list[int]:
    """敌方英雄 id 列表，用于查克制关系。"""
    players = raw_match.get("players")
    if not isinstance(players, list) or not our_side:
        return []
    we_are_radiant = our_side == "radiant"
    out: list[int] = []
    for p in players:
        if not isinstance(p, dict):
            continue
        is_radiant = p.get("isRadiant")
        if is_radiant is None:
            slot = p.get("player_slot")
            is_radiant = isinstance(slot, int) and slot < 128
        if bool(is_radiant) != we_are_radiant:
            hero_id = p.get("hero_id")
            if isinstance(hero_id, int) and hero_id:
                out.append(hero_id)
    return out


def find_raw_player(
    raw_match: dict[str, Any], *, account_id: int | None, hero_id: int | None
) -> dict[str, Any] | None:
    """在原始包里定位某个玩家。优先 account_id，回退 hero_id。

    回退是必要的：未登记的队友 account_id 可能是 None 或匿名，
    但英雄一定对得上（同一局同一边不会有两个相同英雄）。
    """
    players = raw_match.get("players")
    if not isinstance(players, list):
        return None
    if account_id:
        for p in players:
            if isinstance(p, dict) and p.get("account_id") == account_id:
                return p
    if hero_id:
        for p in players:
            if isinstance(p, dict) and p.get("hero_id") == hero_id:
                return p
    return None

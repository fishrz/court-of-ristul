"""L3 诊断层 —— 把事实和基准对撞，产出可执行结论。

职责边界：
    facts.py    取事实（本局你 BKB 28:22）
    meta.py     取基准（同分段该英雄胜局中位 25:00）
    diagnose.py 做判断（晚了 3 分 22 秒，且你的死亡集中在这个窗口）

为什么判断要单独一层，不塞进前两层：
    「什么算晚」是会变的。版本一换、分段一换，同一个 28:22 可能从
    「偏晚」变成「正常」。把阈值和事实提取写在一起，等于把易变的
    东西焊死在稳定的东西上。

反过度解读的三条硬规矩（这是这个模块存在的主要理由）：

    1. 样本不足不发言。
       英雄场次 < 3 不谈个人英雄趋势，meta 场次 < 500 不谈分段胜率。
       拿 2 场胜率说「你这英雄不行」，是用噪音冒充信号。

    2. 只在显著差距发声。
       benchmarks 低于 30 分位或高于 95 分位才值得说。
       85 分位 vs 90 分位本身没有行为含义，硬要解读就是编。

    3. 每条 finding 必须带着「所以下局做什么」。
       说不出下一步动作的观察不是诊断，是废话。
       pings 数量、微小百分位差这类指标就是因此被排除的。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import meta

logger = logging.getLogger(__name__)

# 见模块头规矩 2。这两个阈值是「值得开口」的门槛，不是好坏的分界线。
PCT_BAD = 30.0
PCT_GREAT = 95.0

# 关键装备晚于胜局中位多久才算问题。90 秒以内属于对线波动，
# 不是决策失误，指出来只会让人觉得这 AI 在鸡蛋里挑骨头。
ITEM_LATE_SECONDS = 90

# 躺尸占比。超凡分段一号位正常在 8% 上下，
# 12% 以上意味着每 8 分钟就有 1 分钟不在场上。
DEAD_PCT_BAD = 12.0

BRACKET_NAMES = {
    1: "先驱",
    2: "守卫",
    3: "中军",
    4: "统帅",
    5: "传奇",
    6: "万古",
    7: "超凡",
    8: "不朽",
}


def bracket_label(rank_tier: int | None) -> str:
    bracket = meta.bracket_of(rank_tier)
    if bracket is None:
        return "未知分段"
    name = BRACKET_NAMES.get(bracket, "未知")
    if rank_tier and int(rank_tier) >= 80:
        return "不朽"
    star = int(rank_tier) % 10 if rank_tier else 0
    return f"{name}{star}" if star else name


def _mmss(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _finding(
    kind: str, text: str, action: str, *, severity: str = "info", **extra: Any
) -> dict[str, Any]:
    """所有 finding 的统一形状。action 是必填的——见模块头规矩 3。"""
    return {"kind": kind, "text": text, "action": action, "severity": severity, **extra}


def diagnose_benchmarks(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """百分位诊断。只挑两头，中间一律不说话。

    这里有个刻意的设计：高分位也报。「你经济 97 分位但推塔 60 分位」
    是极有价值的一条——它说明钱没转化成地图控制，而这种问题
    在赢的局里也存在，队内对比永远发现不了。
    """
    bench = facts.get("benchmarks") or {}
    out: list[dict[str, Any]] = []

    lows = [(k, v) for k, v in bench.items() if v.get("pct", 100) < PCT_BAD]
    highs = [(k, v) for k, v in bench.items() if v.get("pct", 0) >= PCT_GREAT]

    for key, item in sorted(lows, key=lambda x: x[1]["pct"]):
        label = item["label"]
        out.append(
            _finding(
                "benchmark_low",
                f"{label} {item['raw']} 只有同英雄 {item['pct']} 分位",
                f"下局把{label}作为唯一盯的指标，目标进 50 分位",
                severity="bad",
                metric=key,
                pct=item["pct"],
            )
        )

    # 强弱并存才是真正有信息量的诊断：强项证明你不是不会玩，
    # 弱项因此更可能是可修的习惯问题而不是能力上限。
    if highs and lows:
        strong = max(highs, key=lambda x: x[1]["pct"])[1]
        weak = min(lows, key=lambda x: x[1]["pct"])[1]
        out.append(
            _finding(
                "benchmark_gap",
                f"{strong['label']}打到 {strong['pct']} 分位，"
                f"{weak['label']}却只有 {weak['pct']} 分位",
                f"你不缺{strong['label']}能力，缺的是把它换成{weak['label']}",
                severity="insight",
            )
        )
    return out


def diagnose_deaths(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """死亡归因。目标是把「你要少死」翻译成「你死在什么手里」。"""
    deaths = facts.get("deaths") or {}
    out: list[dict[str, Any]] = []

    dead_pct = deaths.get("dead_pct")
    if dead_pct is not None and dead_pct >= DEAD_PCT_BAD:
        secs = deaths.get("dead_seconds", 0)
        out.append(
            _finding(
                "dead_time",
                f"躺尸 {secs} 秒，占全局 {dead_pct}%",
                f"这{secs}秒等于白送对面 {secs // 60} 分钟的地图自由",
                severity="bad",
                dead_pct=dead_pct,
            )
        )

    killers = deaths.get("killers") or []
    if killers:
        top = killers[0]
        total = sum(k["count"] for k in killers)
        second = killers[1]["count"] if len(killers) > 1 else 0
        # 「被针对」要同时满足两个条件，缺一不可：
        #   占比够高（40%），且确实比第二名多。
        # 只看占比会误伤：三个凶手各 2 次，占比 33% 看着不低，
        # 但那是团战输了被集火，不是某个英雄专门抓你——
        # 对这种情况说「记住宙斯的CD」是给错药。
        if total >= 5 and top["count"] / total >= 0.4 and top["count"] > second:
            out.append(
                _finding(
                    "killer_focus",
                    f"{top['count']}/{total} 次死亡来自同一个人（{top['hero']}）",
                    f"下局把{top['hero']}的关键技能 CD 记在心里，别在它交完之前露头",
                    severity="bad",
                    killer=top["hero"],
                )
            )
    return out


async def diagnose_items(
    session: AsyncSession, facts: dict[str, Any], hero_id: int
) -> list[dict[str, Any]]:
    """出装节奏诊断。这是最能产出可执行建议的一类。

    「BKB 早出 3 分钟」是玩家下一局真能照做的事，
    比「注意保命」这种建议有用一个数量级。
    """
    timings = (facts.get("items") or {}).get("timings") or {}
    out: list[dict[str, Any]] = []

    for item, actual in sorted(timings.items(), key=lambda x: x[1]):
        ref = await meta.item_timing_median(session, hero_id, item)
        if not ref:
            continue
        median = ref["median_seconds"]
        delay = actual - median
        if delay < ITEM_LATE_SECONDS:
            continue
        out.append(
            _finding(
                "item_late",
                f"{item} {_mmss(actual)} 出，同分段胜局中位 {_mmss(median)}，"
                f"晚了 {delay // 60} 分 {delay % 60} 秒",
                f"下局目标：{_mmss(median)} 前做出 {item}",
                severity="bad",
                item=item,
                actual=actual,
                median=median,
                sample=ref["sample"],
            )
        )
        # 一局里挑最关键的两件就够了，列七件等于没重点
        if len(out) >= 2:
            break
    return out


def diagnose_lane(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """对线期诊断。用逐分钟正补增量定位「哪一分钟开始崩的」。

    累计曲线看不出崩盘，因为它永远单调上升。增量掉到 0
    才是真信号：那一分钟你不在线上，要么死了要么被逼回家。
    """
    curve = facts.get("curve") or {}
    delta = curve.get("lh_delta") or []
    out: list[dict[str, Any]] = []

    if len(delta) >= 10:
        lane_phase = delta[:10]
        zeros = [i + 1 for i, v in enumerate(lane_phase) if v == 0]
        if len(zeros) >= 2:
            out.append(
                _finding(
                    "lane_broken",
                    f"对线期第 {'、'.join(map(str, zeros))} 分钟正补挂零",
                    "这几分钟你不在线上；下局对线优先保证不断线，其次才是补刀数",
                    severity="bad",
                    zero_minutes=zeros,
                )
            )

    eff = curve.get("lane_efficiency_pct")
    if eff is not None and eff < 55:
        out.append(
            _finding(
                "lane_efficiency",
                f"对线效率 {eff}%",
                "对线 10 分钟的经济只拿到理论值的一半多，下局先练稳补刀节奏",
                severity="bad",
            )
        )

    tp = (facts.get("items") or {}).get("tp_bought")
    duration_min = len(curve.get("lh_t") or [])
    # TP 数量单独看是个坏指标，低 TP 至少有三种成因，只有一种是坏习惯：
    #   1. 真的忘了买 —— 这才是我们想抓的
    #   2. 出了飞鞋 —— 自带传送，少买 TP 卷是正确决策
    #   3. 死太多 —— 躺尸时买不了也用不上，低 TP 是死亡的结果不是原因
    # 后两种都得先排除掉，否则就是拿下游症状当根因，
    # 跟「推塔低所以你不推塔」是同一类错误。
    travel_at = (facts.get("items") or {}).get("travel_boots_at")
    dead_pct = (facts.get("deaths") or {}).get("dead_pct")
    tp_noisy = travel_at is not None or (
        dead_pct is not None and dead_pct >= DEAD_PCT_BAD
    )
    if (
        tp is not None
        and duration_min >= 25
        and tp < duration_min / 6
        and not tp_noisy
    ):
        out.append(
            _finding(
                "tp_scarce",
                f"全局只买了 {tp} 个 TP（{duration_min} 分钟的局，且没出飞鞋）",
                "TP 是最便宜的翻盘道具，下局保持身上常备一个",
                severity="warn",
            )
        )
    return out


async def diagnose_matchup(
    session: AsyncSession, hero_id: int, enemy_hero_ids: list[int]
) -> list[dict[str, Any]]:
    """对位诊断。这条的价值是能替玩家洗清冤屈。

    产品判断：法庭偶尔判「无罪，是 BP 的问题」，比场场硬找替罪羊
    可信得多。永远有人被定罪的法庭，玩两周就没人信了。
    """
    bad = await meta.worst_matchups(session, hero_id, enemy_hero_ids)
    if not bad:
        return []
    worst = bad[0]
    return [
        _finding(
            "matchup",
            f"对面有克你的英雄（hero {worst['enemy_hero_id']}），"
            f"这个对位你的英雄全服胜率只有 {worst['winrate']}%",
            "这局的难度有一部分写在 BP 阶段，不全是操作问题",
            severity="mitigating",
            enemy_hero_id=worst["enemy_hero_id"],
            winrate=worst["winrate"],
        )
    ]


async def diagnose_player(
    session: AsyncSession,
    facts: dict[str, Any],
    *,
    hero_id: int,
    enemy_hero_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """单个玩家的完整诊断。按严重度排序，最该看的排前面。"""
    findings: list[dict[str, Any]] = []
    findings += diagnose_benchmarks(facts)
    findings += diagnose_deaths(facts)
    findings += diagnose_lane(facts)
    try:
        findings += await diagnose_items(session, facts, hero_id)
        if enemy_hero_ids:
            findings += await diagnose_matchup(session, hero_id, enemy_hero_ids)
    except Exception as error:  # noqa: BLE001
        # meta 缺失不该让整个诊断失败——本地事实层的结论仍然有效
        logger.warning("meta 诊断跳过：%s", error)

    order = {"bad": 0, "warn": 1, "insight": 2, "mitigating": 3, "info": 4}
    findings.sort(key=lambda f: order.get(f.get("severity", "info"), 9))
    return findings


def summarize_trend(history: list[dict[str, Any]]) -> list[str]:
    """跨局趋势。样本不足直接返回空——见模块头规矩 1。

    history 每项形如 {"dead_pct": float, "bkb": int|None, ...}，
    由调用方从最近 N 场聚合。这里只负责判断「变好还是变坏」。
    """
    if len(history) < 5:
        return []

    out: list[str] = []
    dead = [h["dead_pct"] for h in history if h.get("dead_pct") is not None]
    if len(dead) >= 5:
        half = len(dead) // 2
        early = sum(dead[:half]) / half
        late = sum(dead[half:]) / (len(dead) - half)
        # 3 个百分点以内是正常波动，不值得报「你在退步」
        if late - early >= 3.0:
            out.append(
                f"场均躺尸率从 {early:.0f}% 涨到 {late:.0f}%，最近这几局更容易死"
            )
        elif early - late >= 3.0:
            out.append(f"场均躺尸率从 {early:.0f}% 降到 {late:.0f}%，活得比以前久了")
    return out

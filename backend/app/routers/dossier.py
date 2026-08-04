"""个人卷宗 —— 私密的复盘和成长页数据。

和法庭的区别是刻意的：
    法庭    公开、短、毒、只关心「这局谁的锅」
    卷宗    私密、长、正经、关心「你这个人怎么变强」

同一批事实，两种表达。不能混：把教练报告塞进宣判会毁掉庭审节奏，
把毒舌塞进卷宗会让建议显得不可信。

慢在哪：
    single 走完整诊断链（facts -> meta -> diagnose），meta 有 24h 缓存
    所以通常不打外网。coach() 要 13 秒，因此单独开一个接口按需调用，
    不让卷宗首屏等它。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai, diagnose, facts
from app.db import get_session
from app.models import Match, MatchPlayer, Player, Trial

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dossier", tags=["dossier"])
Session = Annotated[AsyncSession, Depends(get_session)]

# 跨局趋势的最小样本。低于这个数只报单局，不谈「你在进步/退步」——
# 3 场里 2 场躺尸高，那是运气不是趋势。
MIN_TREND_MATCHES = 5

# 英雄池里样本低于这个数的，界面必须显式标「样本不足」，
# 不能拿 1 场 100% 胜率去糊弄人。
MIN_HERO_SAMPLE = 3


async def _load_player(session: AsyncSession, player_id: int) -> Player:
    player = await session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="player not found")
    return player


async def _player_rows(
    session: AsyncSession, player_id: int, limit: int = 30
) -> list[tuple[MatchPlayer, Match]]:
    """该玩家参与过的比赛，新的在前。

    join 而不是两次查询，是因为诊断需要 raw_json（在 Match 上）
    和该玩家这局的英雄（在 MatchPlayer 上），两个都要。
    """
    stmt = (
        select(MatchPlayer, Match)
        .join(Match, MatchPlayer.match_id == Match.id)
        .where(MatchPlayer.player_id == player_id)
        .order_by(Match.started_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


def _player_facts(match: Match, steam_id: int, hero_id: int) -> dict[str, Any] | None:
    """从这局的原始包里取该玩家的事实。没解析的局返回 None。"""
    if not match.raw_json:
        return None
    try:
        raw = json.loads(match.raw_json)
    except (TypeError, ValueError):
        return None
    raw_player = facts.find_raw_player(raw, account_id=steam_id, hero_id=hero_id)
    if raw_player is None:
        return None
    return facts.extract_player_facts(raw_player)


@router.get("/{player_id}")
async def dossier(player_id: int, session: Session) -> dict[str, Any]:
    """卷宗首屏：身份 + 战绩概览 + 英雄池 + 趋势。

    刻意不含 LLM 调用——首屏必须秒开。教练意见走 /coach 按需拉。
    """
    player = await _load_player(session, player_id)
    rows = await _player_rows(session, player_id)

    wins = sum(1 for _, match in rows if match.we_won)
    total = len(rows)

    # 英雄池：场次 + 胜率，样本不足的照实标出来
    hero_counter: Counter[tuple[int, str]] = Counter()
    hero_wins: Counter[tuple[int, str]] = Counter()
    for mp, match in rows:
        key = (mp.hero_id, mp.hero_name or str(mp.hero_id))
        hero_counter[key] += 1
        if match.we_won:
            hero_wins[key] += 1

    heroes = []
    for (hero_id, hero_name), count in hero_counter.most_common(8):
        enough = count >= MIN_HERO_SAMPLE
        heroes.append(
            {
                "hero_id": hero_id,
                "hero_name": hero_name,
                "matches": count,
                "wins": hero_wins[(hero_id, hero_name)],
                # winrate 只在样本够时给，否则前端会忍不住显示出来
                "winrate": (
                    round(hero_wins[(hero_id, hero_name)] / count * 100, 1)
                    if enough
                    else None
                ),
                "enough_sample": enough,
            }
        )

    # 趋势：需要逐局算躺尸率，只在样本够时做
    trend: list[str] = []
    rank_tier = None
    if total >= MIN_TREND_MATCHES:
        history = []
        # 按时间正序，趋势才有方向
        for mp, match in reversed(rows):
            fact = _player_facts(match, player.steam_id, mp.hero_id)
            if not fact:
                continue
            if rank_tier is None:
                rank_tier = fact.get("rank_tier")
            dead_pct = (fact.get("deaths") or {}).get("dead_pct")
            if dead_pct is not None:
                history.append({"dead_pct": dead_pct})
        trend = diagnose.summarize_trend(history)

    if rank_tier is None and rows:
        first = _player_facts(rows[0][1], player.steam_id, rows[0][0].hero_id)
        rank_tier = (first or {}).get("rank_tier")

    # 被判次数：卷宗要诚实，赢了输了都得认
    conviction_count = len(
        (
            await session.scalars(
                select(Trial.id).where(Trial.verdict_player_id == player_id)
            )
        ).all()
    )

    # 最近比赛列表：卷宗页要能点开任意一局看诊断
    recent = [
        {
            "match_id": match.match_id,
            "hero_id": mp.hero_id,
            "hero_name": mp.hero_name or str(mp.hero_id),
            "we_won": match.we_won,
            "duration": match.duration,
            "kills": mp.kills,
            "deaths": mp.deaths,
            "assists": mp.assists,
            "started_at": match.started_at.isoformat() if match.started_at else None,
            "parsed": bool(match.raw_json),
        }
        for mp, match in rows[:15]
    ]

    return {
        "player": {
            "id": player.id,
            "steam_id": player.steam_id,
            "display_name": player.display_name,
            "avatar_url": player.avatar_url,
            "is_active": player.is_active,
        },
        "bracket": diagnose.bracket_label(rank_tier),
        "rank_tier": rank_tier,
        "record": {
            "matches": total,
            "wins": wins,
            "losses": total - wins,
            "winrate": round(wins / total * 100, 1) if total else None,
            "convictions": conviction_count,
            "has_conviction": conviction_count > 0,
        },
        "heroes": heroes,
        "recent": recent,
        "trend": trend,
        "trend_available": total >= MIN_TREND_MATCHES,
        "min_trend_matches": MIN_TREND_MATCHES,
    }


@router.get("/{player_id}/match/{match_id}")
async def dossier_match(
    player_id: int, match_id: int, session: Session
) -> dict[str, Any]:
    """单局深度诊断。这是卷宗的核心——真正告诉你这局哪里出了问题。"""
    player = await _load_player(session, player_id)

    stmt = (
        select(MatchPlayer, Match)
        .join(Match, MatchPlayer.match_id == Match.id)
        .where(MatchPlayer.player_id == player_id, Match.match_id == match_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="match not found for this player")
    mp, match = row[0], row[1]

    fact = _player_facts(match, player.steam_id, mp.hero_id)
    if not fact:
        # 没解析完的局没有过程数据。诚实地说没有，而不是拿汇总数据凑。
        return {
            "match_id": match_id,
            "hero_name": mp.hero_name,
            "parsed": False,
            "findings": [],
            "note": "这局还没解析完，暂时没有过程数据可分析",
        }

    enemies = []
    if match.raw_json:
        try:
            enemies = facts.enemy_hero_ids(json.loads(match.raw_json), match.our_side)
        except (TypeError, ValueError):
            enemies = []

    findings = await diagnose.diagnose_player(
        session, fact, hero_id=mp.hero_id, enemy_hero_ids=enemies
    )

    return {
        "match_id": match_id,
        "hero_id": mp.hero_id,
        "hero_name": mp.hero_name,
        "we_won": match.we_won,
        "duration": match.duration,
        "bracket": diagnose.bracket_label(fact.get("rank_tier")),
        "parsed": True,
        "kda": {"k": mp.kills, "d": mp.deaths, "a": mp.assists},
        "benchmarks": fact.get("benchmarks") or {},
        "deaths": fact.get("deaths") or {},
        "items": fact.get("items") or {},
        "findings": findings,
    }


@router.post("/{player_id}/match/{match_id}/coach")
async def dossier_coach(
    player_id: int, match_id: int, session: Session
) -> dict[str, Any]:
    """教练意见。单独接口是因为它要 13 秒——不能拖慢卷宗首屏。

    失败一律降级成 available=false，前端继续显示规则层的 findings。
    宁可没有教练意见，也不能让页面挂掉或显示「AI 故障」吓用户。
    """
    detail = await dossier_match(player_id, match_id, session)
    if not detail.get("parsed"):
        return {"available": False, "reason": "unparsed"}

    player = await _load_player(session, player_id)
    duration = detail.get("duration") or 0

    stmt = (
        select(MatchPlayer, Match)
        .join(Match, MatchPlayer.match_id == Match.id)
        .where(MatchPlayer.player_id == player_id, Match.match_id == match_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="match not found")
    mp, match = row[0], row[1]
    fact = _player_facts(match, player.steam_id, mp.hero_id) or {}

    result = await ai.coach(
        player_name=player.display_name,
        hero=mp.hero_name or str(mp.hero_id),
        role=None,
        bracket_label=detail.get("bracket") or "未知分段",
        we_won=bool(detail.get("we_won")),
        duration=f"{duration // 60}:{duration % 60:02d}",
        facts=fact,
        findings=detail.get("findings") or [],
    )
    if result is None:
        return {"available": False, "reason": "llm_unavailable"}
    return {"available": True, **result}

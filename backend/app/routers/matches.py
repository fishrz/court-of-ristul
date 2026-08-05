import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Match, Player, Trial
from app.opendota import OpenDotaClient
from app.poller import poll_once
from app.schemas import MatchListItem, MatchPlayerRead, MatchRead

router = APIRouter(tags=["matches"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/api/matches", response_model=list[MatchListItem])
async def list_matches(
    session: Session,
    filter: Literal["win", "lose", "pending"] | None = None,
) -> list[MatchListItem]:
    statement = select(Match)
    if filter == "win":
        statement = statement.where(Match.we_won.is_(True))
    elif filter == "lose":
        statement = statement.where(Match.we_won.is_(False))
    elif filter == "pending":
        statement = statement.where(Match.parse_status.in_(["pending", "parsing"]))
    statement = statement.order_by(Match.started_at.desc(), Match.created_at.desc())
    statement = statement.options(selectinload(Match.players))
    cases = list(await session.scalars(statement))

    # 判决结果：一局只能开庭一次，所以 match_id -> trial 是一对一
    trials = {
        trial.match_id: trial
        for trial in await session.scalars(
            select(Trial).where(Trial.match_id.in_([case.id for case in cases]))
        )
    } if cases else {}
    names = {
        player.id: player.display_name
        for player in await session.scalars(select(Player))
    }

    items = []
    for case in cases:
        item = MatchListItem.model_validate(case)
        item.heroes = [
            player.hero_name
            for player in case.players
            if player.is_our_team and player.hero_name
        ]
        trial = trials.get(case.id)
        item.trial_id = trial.id if trial is not None else None
        item.trial_status = trial.status if trial is not None else None
        if trial is not None and trial.verdict_player_id is not None:
            item.verdict_name = names.get(trial.verdict_player_id)
            item.verdict_note = _verdict_note(trial)
        items.append(item)
    return items


def _verdict_note(trial: Trial) -> str | None:
    """判决摘要：取首条罪证的 tag，给案卷卡当一句话说明。"""
    payload = _json_value(trial.verdict_json)
    if isinstance(payload, dict):
        evidence = payload.get("evidence")
        if isinstance(evidence, list) and evidence:
            first = evidence[0]
            if isinstance(first, dict):
                return first.get("tag") or first.get("fact")
    return None


@router.get("/api/matches/{match_id}")
async def match_detail(
    match_id: int, session: Session
) -> dict[str, Any]:
    case = await session.scalar(
        select(Match)
        .where(Match.match_id == match_id)
        .options(selectinload(Match.players))
    )
    if case is None:
        raise HTTPException(status_code=404, detail="match not found")
    payload = MatchRead.model_validate(case).model_dump(mode="json")
    # 已解析成对象的字段不再重复传原始字符串；raw_json 是几百 KB 的
    # OpenDota 原始包，前端用不到，留在库里备查即可。
    payload.pop("raw_json", None)
    payload.pop("evidence_json", None)
    payload.pop("nominees_json", None)
    payload["players"] = [
        MatchPlayerRead.model_validate(player).model_dump(mode="json")
        for player in case.players
    ]
    payload["evidence"] = _json_value(case.evidence_json)
    payload["nominees"] = _json_value(case.nominees_json)
    payload["timeline"] = _timeline(case)
    return payload


def _timeline(case: Match) -> dict[str, Any] | None:
    """从 OpenDota 原始包里提取真实团战时间线。

    前端原来是一段写死的设计稿：8 根固定高度的柱子 + 写死的
    「20:04 一波团灭」。每场比赛长得一模一样，等于伪造证据。

    raw_json 有 teamfights（起止秒、死亡数、每人 gold_delta）和
    radiant_gold_adv（逐分钟经济差），够还原真实战况。
    未解析的比赛没有这些字段，返回 None，前端隐藏该模块。
    """
    if not case.raw_json:
        return None
    try:
        data = json.loads(case.raw_json)
    except (ValueError, TypeError):
        return None

    fights_raw = data.get("teamfights")
    if not isinstance(fights_raw, list) or not fights_raw:
        return None

    # 经济差是以天辉视角给的，我方在夜魇时要取反，否则转折点方向是反的
    gold_adv = data.get("radiant_gold_adv")
    if not isinstance(gold_adv, list):
        gold_adv = []
    flip = case.our_side == "dire"

    def our_gold_at(second: int) -> int | None:
        # 团战可能从负数秒开始（赛前 -90s 的选人/买装阶段），
        # Python 的 // 对负数向下取整会取到 gold_adv[-1]，也就是终局经济差，
        # 于是赛前小规模接触会显示成两万经济摆动。钳到 0。
        minute = max(second, 0) // 60
        if minute >= len(gold_adv):
            return None
        value = gold_adv[minute]
        if not isinstance(value, (int, float)):
            return None
        return int(-value if flip else value)

    fights: list[dict[str, Any]] = []
    for raw in fights_raw:
        if not isinstance(raw, dict):
            continue
        start = raw.get("start")
        if not isinstance(start, (int, float)):
            continue
        start = max(int(start), 0)
        deaths = int(raw.get("deaths") or 0)
        before = our_gold_at(start)
        after = our_gold_at(max(int(raw.get("end") or start), 0))
        swing = (after - before) if (before is not None and after is not None) else None
        fights.append(
            {
                "start": start,
                "label": f"{start // 60}'",
                "deaths": deaths,
                "gold_before": before,
                "gold_after": after,
                "swing": swing,
            }
        )
    if not fights:
        return None

    # 转折点 = 经济摆动最狠的那一场，而不是死人最多的那场。
    # 团灭对面和被团灭都死 10 人，只有经济方向能区分死得值不值。
    #
    # 但要排除最后一波：输的那局最后一团必然巨亏（对面正在推高地），
    # 那是结果不是原因。转折点应该是「从这里开始不对劲」的那一场。
    pivot = None
    candidates = [f for f in fights[:-1] if f["swing"] is not None]
    worst = min(candidates, key=lambda f: f["swing"], default=None)
    # 门槛 1500：赢的局也总有一两波小亏，标成「转折点」是过度解读。
    # 没有真正打崩的一波就不给转折点，前端隐藏那行结论。
    if worst is not None and worst["swing"] <= -1500:
        pivot = worst

    return {
        "count": len(fights),
        "fights": fights,
        "pivot": pivot,
        "duration": case.duration,
    }


def _json_value(value: str | None) -> object | None:
    return json.loads(value) if value else None


@router.post("/api/matches/sync")
async def sync_matches(session: Session) -> dict[str, int]:
    async with OpenDotaClient() as client:
        created = await poll_once(session, client)
    return {"created": created}


@router.get("/api/stats/monthly")
async def monthly_stats(session: Session) -> dict[str, Any]:
    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    next_month = (
        datetime(now.year + 1, 1, 1, tzinfo=UTC)
        if now.month == 12
        else datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    )
    period = (
        Trial.status == "closed",
        Trial.closed_at >= month_start,
        Trial.closed_at < next_month,
    )
    trials = int(
        await session.scalar(
            select(func.count(Trial.id))
            .join(Match, Trial.match_id == Match.id)
            .where(*period)
        )
        or 0
    )
    wins = int(
        await session.scalar(
            select(func.count(Trial.id))
            .join(Match, Trial.match_id == Match.id)
            .where(*period, Match.we_won.is_(True))
        )
        or 0
    )
    guilty = int(
        await session.scalar(
            select(func.count(Trial.id))
            .join(Match, Trial.match_id == Match.id)
            .where(
                *period,
                Match.we_won.is_(False),
                Trial.verdict_player_id.is_not(None),
            )
        )
        or 0
    )

    async def leaderboard_for(we_won: bool) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(
                    Player.id,
                    Player.display_name,
                    func.count(Trial.id).label("count"),
                )
                .join(Trial, Trial.verdict_player_id == Player.id)
                .join(Match, Trial.match_id == Match.id)
                .where(*period, Match.we_won.is_(we_won))
                .group_by(Player.id, Player.display_name)
                .order_by(func.count(Trial.id).desc(), Player.id)
            )
        ).all()
        return [
            {"player_id": player_id, "display_name": display_name, "count": count}
            for player_id, display_name, count in rows
        ]

    return {
        "trials": trials,
        "wins": wins,
        "guilty": guilty,
        "leaderboard": await leaderboard_for(False),
        "mvp_leaderboard": await leaderboard_for(True),
    }

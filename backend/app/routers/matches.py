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
    return payload


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
    guilty = int(
        await session.scalar(
            select(func.count(Trial.id)).where(
                Trial.status == "closed",
                Trial.verdict_player_id.is_not(None),
                Trial.closed_at >= month_start,
                Trial.closed_at < next_month,
            )
        )
        or 0
    )
    wins = int(
        await session.scalar(
            select(func.count(Trial.id))
            .where(
                Trial.status == "closed",
                Trial.verdict_player_id.is_(None),
                Trial.closed_at >= month_start,
                Trial.closed_at < next_month,
            )
        )
        or 0
    )
    rows = (
        await session.execute(
            select(
                Player.id,
                Player.display_name,
                func.count(Trial.id).label("count"),
            )
            .join(Trial, Trial.verdict_player_id == Player.id)
            .where(
                Trial.status == "closed",
                Trial.closed_at >= month_start,
                Trial.closed_at < next_month,
            )
            .group_by(Player.id, Player.display_name)
            .order_by(func.count(Trial.id).desc(), Player.id)
        )
    ).all()
    leaderboard = [
        {"player_id": player_id, "display_name": display_name, "count": count}
        for player_id, display_name, count in rows
    ]
    return {
        "trials": wins + guilty,
        "wins": wins,
        "guilty": guilty,
        "leaderboard": leaderboard,
    }
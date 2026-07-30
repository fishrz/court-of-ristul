import asyncio
import json
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import SessionLocal, get_session
from app.models import Attendance, Match, MatchPlayer, Trial, Vote
from app.schemas import AppealCreate, TrialRead
from app.ws import manager

router = APIRouter(tags=["trials"])
Session = Annotated[AsyncSession, Depends(get_session)]
VOTE_SECONDS = 60


class AttendanceCreate(BaseModel):
    player_id: int = Field(gt=0)


class VoteRequest(BaseModel):
    voter_id: int = Field(gt=0)
    nominee_id: int = Field(gt=0)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _json(value: str | None) -> Any:
    return json.loads(value) if value else None


def _trial_payload(trial: Trial, tally: dict[str, int]) -> dict[str, Any]:
    payload = TrialRead.model_validate(trial).model_dump(mode="json")
    payload.update(
        attendances=[item.player_id for item in trial.attendances],
        here=len(trial.attendances),
        total=len([player for player in trial.match.players if player.is_our_team]),
        tally=tally,
        ai_verdict=_json(trial.ai_verdict_json),
        verdict=_json(trial.verdict_json),
        ai_agrees=(
            trial.verdict_player_id == trial.ai_verdict_player_id
            if trial.status == "closed" and trial.verdict_player_id is not None
            else None
        ),
    )
    return payload


async def _get_trial(session: AsyncSession, trial_id: int) -> Trial:
    trial = await session.scalar(
        select(Trial)
        .where(Trial.id == trial_id)
        .options(
            selectinload(Trial.attendances),
            selectinload(Trial.votes),
            selectinload(Trial.match).selectinload(Match.players),
        )
    )
    if trial is None:
        raise HTTPException(status_code=404, detail="trial not found")
    return trial


def _tally(votes: list[Vote]) -> dict[str, int]:
    counts = Counter(vote.nominee_id for vote in votes)
    return {str(player_id): counts[player_id] for player_id in sorted(counts)}


def _ai_verdict(case: Match) -> tuple[int, str]:
    result = _json(case.nominees_json)
    suspects = result.get("suspects", []) if isinstance(result, dict) else []
    known_by_steam_id = {
        item.player.steam_id: item.player_id
        for item in case.players
        if item.is_our_team and item.player_id is not None and item.player is not None
    }
    eligible = []
    for suspect in suspects:
        external_id = suspect.get("player", {}).get("id")
        player_id = known_by_steam_id.get(external_id)
        if player_id is not None:
            eligible.append((suspect, player_id))
    if not eligible:
        raise HTTPException(status_code=409, detail="match has no attributable nominees")
    top, player_id = max(eligible, key=lambda item: item[0].get("score", 0))
    verdict = next(
        (
            evidence.get("verdict")
            for evidence in top.get("evidence", [])
            if evidence.get("verdict")
        ),
        None,
    )
    return player_id, json.dumps(
        {
            "score": top.get("score", 0),
            "evidence": top.get("evidence", []),
            "reasoning": verdict or "AI 根据已验证比赛指标选择最高归因分玩家",
        },
        ensure_ascii=False,
    )


@router.post("/api/trials/{match_id}/open")
async def open_trial(match_id: int, session: Session) -> dict[str, Any]:
    case = await session.scalar(
        select(Match)
        .where(Match.match_id == match_id)
        .options(selectinload(Match.players).selectinload(MatchPlayer.player))
    )
    if case is None:
        raise HTTPException(status_code=404, detail="match not found")
    if case.parse_status != "parsed":
        raise HTTPException(status_code=409, detail="match is not parsed")
    if await session.scalar(select(Trial.id).where(Trial.match_id == case.id)):
        raise HTTPException(status_code=409, detail="trial already exists")

    ai_player_id, ai_json = _ai_verdict(case)
    trial = Trial(
        match_id=case.id,
        status="waiting",
        ai_verdict_player_id=ai_player_id,
        ai_verdict_json=ai_json,
    )
    session.add(trial)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail="trial already exists") from error
    trial = await _get_trial(session, trial.id)
    return _trial_payload(trial, {})


@router.get("/api/trials/{trial_id}")
async def trial_state(trial_id: int, session: Session) -> dict[str, Any]:
    trial = await _get_trial(session, trial_id)
    return _trial_payload(trial, _tally(trial.votes))


@router.post("/api/trials/{trial_id}/attend")
async def attend(
    trial_id: int, body: AttendanceCreate, session: Session
) -> dict[str, Any]:
    trial = await _get_trial(session, trial_id)
    eligible = {
        player.player_id
        for player in trial.match.players
        if player.is_our_team and player.player_id is not None
    }
    if body.player_id not in eligible:
        raise HTTPException(status_code=422, detail="player is not in this match")
    attendance = await session.scalar(
        select(Attendance).where(
            Attendance.trial_id == trial_id,
            Attendance.player_id == body.player_id,
        )
    )
    if attendance is None:
        session.add(Attendance(trial_id=trial_id, player_id=body.player_id))
        await session.commit()
    here = int(
        await session.scalar(
            select(func.count(Attendance.id)).where(Attendance.trial_id == trial_id)
        )
        or 0
    )
    event = {
        "type": "attend",
        "player_id": body.player_id,
        "here": here,
        "total": len(eligible),
    }
    await manager.broadcast(trial_id, event)
    if here == len(eligible) and trial.status == "waiting":
        trial.status = "evidence"
        await session.commit()
        await manager.broadcast(trial_id, {"type": "stage", "stage": "evidence"})
    return event


@router.post("/api/trials/{trial_id}/start-vote")
async def start_vote(trial_id: int, session: Session) -> dict[str, Any]:
    trial = await _get_trial(session, trial_id)
    if trial.status == "closed":
        raise HTTPException(status_code=409, detail="trial is closed")
    if trial.status == "voting" and trial.vote_deadline is not None:
        return {
            "type": "vote_start",
            "deadline": _aware(trial.vote_deadline).isoformat().replace("+00:00", "Z"),
        }
    if not trial.attendances:
        raise HTTPException(status_code=409, detail="no players attended")
    started = _now()
    deadline = started + timedelta(seconds=VOTE_SECONDS)
    claimed = await session.execute(
        update(Trial)
        .where(Trial.id == trial_id, Trial.status == trial.status)
        .values(
            status="voting",
            vote_started_at=started,
            vote_deadline=deadline,
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    if claimed.rowcount != 1:
        session.expire_all()
        trial = await _get_trial(session, trial_id)
        if trial.status == "voting" and trial.vote_deadline is not None:
            return {
                "type": "vote_start",
                "deadline": _aware(trial.vote_deadline)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        raise HTTPException(status_code=409, detail="voting is not ready")

    asyncio.create_task(_settle_at_deadline(trial_id, deadline))
    event = {
        "type": "vote_start",
        "deadline": deadline.isoformat().replace("+00:00", "Z"),
    }
    await manager.broadcast(trial_id, event)
    return event


@router.post("/api/trials/{trial_id}/vote")
async def cast_vote(
    trial_id: int, body: VoteRequest, session: Session
) -> dict[str, Any]:
    trial = await _get_trial(session, trial_id)
    if trial.status != "voting" or trial.vote_deadline is None:
        raise HTTPException(status_code=409, detail="voting is not open")
    if _now() >= _aware(trial.vote_deadline):
        await _settle(session, trial)
        raise HTTPException(status_code=409, detail="vote deadline passed")
    attendees = {attendance.player_id for attendance in trial.attendances}
    nominees = {
        player.player_id
        for player in trial.match.players
        if player.is_our_team and player.player_id is not None
    }
    if body.voter_id not in attendees:
        raise HTTPException(status_code=422, detail="voter has not attended")
    if body.nominee_id not in nominees:
        raise HTTPException(status_code=422, detail="nominee is not in this match")

    vote = next((item for item in trial.votes if item.voter_id == body.voter_id), None)
    if vote is None:
        vote = Vote(
            trial_id=trial_id,
            voter_id=body.voter_id,
            nominee_id=body.nominee_id,
        )
        session.add(vote)
        trial.votes.append(vote)
    else:
        vote.nominee_id = body.nominee_id
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        vote = await session.scalar(
            select(Vote).where(
                Vote.trial_id == trial_id,
                Vote.voter_id == body.voter_id,
            )
        )
        if vote is None:
            raise
        vote.nominee_id = body.nominee_id
        await session.commit()
    trial = await _get_trial(session, trial_id)
    tally = _tally(trial.votes)
    event = {
        "type": "vote",
        "voter_id": body.voter_id,
        "nominee_id": body.nominee_id,
        "tally": tally,
    }
    await manager.broadcast(trial_id, event)
    if len(trial.votes) == len(attendees):
        await _settle(session, trial)
    return event


@router.post("/api/trials/{trial_id}/appeal")
async def appeal(
    trial_id: int, body: AppealCreate, session: Session
) -> dict[str, Any]:
    trial = await _get_trial(session, trial_id)
    trial.appeal_text = body.text
    await session.commit()
    event = {"type": "appeal", "text": body.text}
    await manager.broadcast(trial_id, event)
    return event


async def _settle(session: AsyncSession, trial: Trial) -> dict[str, Any] | None:
    if trial.status == "closed":
        return None
    tally = _tally(trial.votes)
    counts = Counter(vote.nominee_id for vote in trial.votes)
    guilty_player_id = None
    if counts:
        highest = max(counts.values())
        leaders = [player_id for player_id, count in counts.items() if count == highest]
        guilty_player_id = (
            trial.ai_verdict_player_id
            if len(leaders) > 1
            else leaders[0]
        )
    ai = _json(trial.ai_verdict_json) or {}
    verdict_text = ai.get("reasoning") or "投票结束"
    trial.status = "closed"
    trial.verdict_player_id = guilty_player_id
    trial.verdict_json = json.dumps(
        {"guilty_player_id": guilty_player_id, "tally": tally, "verdict": verdict_text},
        ensure_ascii=False,
    )
    trial.closed_at = _now()
    await session.commit()
    event = {
        "type": "verdict",
        "guilty_player_id": guilty_player_id,
        "tally": tally,
        "ai_verdict_player_id": trial.ai_verdict_player_id,
        "ai_agrees": guilty_player_id == trial.ai_verdict_player_id,
        "verdict": verdict_text,
    }
    await manager.broadcast(trial.id, event)
    return event


async def _settle_at_deadline(trial_id: int, deadline: datetime) -> None:
    delay = max(0.0, (_aware(deadline) - _now()).total_seconds())
    await asyncio.sleep(delay)
    async with SessionLocal() as session:
        trial = await _get_trial(session, trial_id)
        await _settle(session, trial)


@router.websocket("/ws/trials/{trial_id}")
async def trial_websocket(websocket: WebSocket, trial_id: int) -> None:
    await manager.connect(trial_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(trial_id, websocket)
        with suppress(RuntimeError):
            await websocket.close()

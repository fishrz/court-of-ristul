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

from app import ai
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
        # 投票明细：让客户端在重连后能还原"谁投了谁"，
        # 仅有聚合 tally 时无法判断本人是否已投过票。
        votes=[
            {"voter_id": vote.voter_id, "nominee_id": vote.nominee_id}
            for vote in trial.votes
        ],
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
    # 归因引擎输出的 player.id 口径不唯一：轮询入库时是 OpenDota account_id，
    # 而以归一化 fixture 直接喂 engine.accuse 时只是 1..5 的序号。
    # 只按 steam_id 查会整局匹配不上，导致本可开庭的比赛报 409。
    # 因此再建一份按我方出场顺序的序号映射作为回退。
    our_players = [
        item for item in case.players if item.is_our_team and item.player_id is not None
    ]
    known_by_ordinal = {
        ordinal: item.player_id for ordinal, item in enumerate(our_players, start=1)
    }
    eligible = []
    for suspect in suspects:
        external_id = suspect.get("player", {}).get("id")
        player_id = known_by_steam_id.get(external_id)
        if player_id is None:
            player_id = known_by_ordinal.get(external_id)
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
    # 幂等：一局只有一场庭，五个人各自点"开庭"都应进同一场，
    # 而不是让后到的人拿 409 然后各跑各的。
    existing_id = await session.scalar(
        select(Trial.id).where(Trial.match_id == case.id)
    )
    if existing_id:
        trial = await _get_trial(session, existing_id)
        return _trial_payload(trial, _tally(trial.votes))

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
        # 并发下另一个请求先建好了：同样返回既有那场
        await session.rollback()
        existing_id = await session.scalar(
            select(Trial.id).where(Trial.match_id == case.id)
        )
        if not existing_id:
            raise HTTPException(status_code=409, detail="trial already exists") from error
        trial = await _get_trial(session, existing_id)
        return _trial_payload(trial, _tally(trial.votes))
    trial = await _get_trial(session, trial.id)
    # LLM 判词要 ~13s，绝不能挡住开庭响应（五个人同时点开庭）。
    # 丢到后台跑，完成后走 WebSocket 广播覆盖；投票期 60s，来得及。
    _spawn_ai_opinion(trial.id, case)
    return _trial_payload(trial, {})


# 持有后台任务的强引用，否则 asyncio 可能在任务跑完前把它回收掉。
_AI_TASKS: set[asyncio.Task[Any]] = set()


def _spawn_ai_opinion(trial_id: int, case: Match) -> None:
    # 在这里把 ORM 对象拍平成纯数据再交给后台任务：session 会随请求关闭，
    # 后台协程里再碰 case.players 会触发已关闭 session 的懒加载而炸掉。
    #
    # 只喂 player_id 非空的队友：未登记的人无法被投票，也无法作为
    # verdict_player_id 落库。若放进候选，模型很可能判中一个没登记的人
    # （实测就judged中了未登记的黑刺），写回后 ai_verdict_player_id 变成
    # None，宣判页直接失去被告。规则引擎的 _ai_verdict 同样只认可归属的人。
    ours = [
        item
        for item in case.players
        if item.is_our_team and item.player_id is not None
    ]
    if not ours:
        return
    players = []
    for item in ours:
        metrics = _json(item.metrics_json) or {}
        players.append(
            {
                "player_id": item.player_id,
                "name": (item.player.display_name if item.player else None) or "玩家",
                "hero": item.hero_name,
                "role": item.lane_role,
                "kills": item.kills,
                "deaths": item.deaths,
                "assists": item.assists,
                "gpm": item.gpm,
                "teamfight": item.teamfight_participation,
                "damage": item.damage_share,
                "lh10": item.lh_at_10,
                "obs": metrics.get("obs_placed"),
            }
        )
    duration = f"{case.duration // 60}:{case.duration % 60:02d}" if case.duration else "—"
    task = asyncio.create_task(
        _run_ai_opinion(
            trial_id=trial_id,
            we_won=bool(case.we_won),
            duration=duration,
            players=players,
        )
    )
    _AI_TASKS.add(task)
    task.add_done_callback(_AI_TASKS.discard)


async def _run_ai_opinion(
    *, trial_id: int, we_won: bool, duration: str, players: list[dict[str, Any]]
) -> None:
    async with SessionLocal() as session:
        trial = await session.get(Trial, trial_id)
        if trial is None:
            return
        # 规则引擎选中的人在我方名单里的序号，作为给模型的参考
        rule_pick = next(
            (
                i
                for i, p in enumerate(players, start=1)
                if p["player_id"] == trial.ai_verdict_player_id
            ),
            None,
        )
        result = await ai.judge(
            we_won=we_won, duration=duration, players=players, rule_pick=rule_pick
        )
        if result is None:
            return  # 静默降级：规则引擎判词已经在库里了

        chosen = players[result["guilty"] - 1]
        # 防御：候选已过滤过 player_id，但判词写回前再确认一次。
        # ai_verdict_player_id 一旦为 None，宣判页就没有被告了。
        if chosen["player_id"] is None:
            return
        existing = _json(trial.ai_verdict_json) or {}
        # 保留规则引擎的 evidence/score，只换判词，并如实记录是否分歧。
        # 分歧本身是产品看点，不要抹平成一致。
        existing.update(
            reasoning=result["reason"],
            advice=result.get("advice") or "",
            source="deepseek",
            rule_pick_player_id=trial.ai_verdict_player_id,
            overruled=chosen["player_id"] != trial.ai_verdict_player_id,
        )
        trial.ai_verdict_player_id = chosen["player_id"]
        trial.ai_verdict_json = json.dumps(existing, ensure_ascii=False)
        await session.commit()

        await manager.broadcast(
            trial_id,
            {
                "type": "ai_opinion",
                "ai_verdict_player_id": chosen["player_id"],
                "reason": result["reason"],
                "advice": result.get("advice") or "",
                "overruled": existing["overruled"],
            },
        )


@router.get("/api/trials/{trial_id}")
async def trial_state(trial_id: int, session: Session) -> dict[str, Any]:
    trial = await _get_trial(session, trial_id)
    # 惰性结算：即使定时器丢失（重启）或全员离线无人再投票，
    # 只要有人读取状态，超时的审判就会被正确结案，不会永远卡在 voting。
    if (
        trial.status == "voting"
        and trial.vote_deadline is not None
        and _now() >= _aware(trial.vote_deadline)
    ):
        await _settle(session, trial)
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
    if trial.status != "evidence":
        raise HTTPException(status_code=409, detail="voting is not ready")
    if not trial.attendances:
        raise HTTPException(status_code=409, detail="no players attended")
    started = _now()
    deadline = started + timedelta(seconds=VOTE_SECONDS)
    claimed = await session.execute(
        update(Trial)
        .where(Trial.id == trial_id, Trial.status == "evidence")
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
    ai_data = _json(trial.ai_verdict_json) or {}
    verdict_text = ai_data.get("reasoning") or "投票结束"
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
        # 书记官的改进建议：只有 LLM 会产出，规则引擎没有，前端自行判空
        "advice": ai_data.get("advice") or "",
    }
    await manager.broadcast(trial.id, event)
    return event


async def _settle_at_deadline(trial_id: int, deadline: datetime) -> None:
    delay = max(0.0, (_aware(deadline) - _now()).total_seconds())
    await asyncio.sleep(delay)
    async with SessionLocal() as session:
        trial = await _get_trial(session, trial_id)
        # 这个任务可能是上一局遗留的：主键被复用、或投票被重开过。
        # 只有当 trial 仍在投票中、且 deadline 与本任务当初排定的一致时才结算，
        # 否则一个陈旧的 timer 会把另一局提前判掉。
        if trial.status != "voting" or trial.vote_deadline is None:
            return
        if _aware(trial.vote_deadline) != _aware(deadline):
            return
        await _settle(session, trial)


async def settle_overdue_trials() -> int:
    """服务启动时补结算：内存中的 deadline 任务不会在重启后存活，
    没有这一步，重启时正在投票的审判会永远停在 voting。"""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Trial).where(Trial.status == "voting")
        )
        settled = 0
        for row in result.scalars().all():
            deadline = row.vote_deadline
            if deadline is None:
                continue
            remaining = (_aware(deadline) - _now()).total_seconds()
            trial = await _get_trial(session, row.id)
            if remaining <= 0:
                await _settle(session, trial)
                settled += 1
            else:
                # 还没到点，重建定时器
                asyncio.create_task(_settle_at_deadline(trial.id, deadline))
        return settled


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

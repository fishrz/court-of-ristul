import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_session
from app.models import Attendance, Match, MatchPlayer, Player, Trial
from app.routers import trials


@pytest.fixture
def quorum_app(tmp_path, monkeypatch) -> Iterator[tuple[TestClient, async_sessionmaker]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quorum.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_session():
        async with factory() as session:
            yield session

    async def no_settlement(_trial_id, _deadline):
        return None

    monkeypatch.setattr(trials, "_settle_at_deadline", no_settlement)
    asyncio.run(prepare())
    app = FastAPI()
    app.include_router(trials.router)
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, factory
    asyncio.run(engine.dispose())


async def _seed_trial(factory: async_sessionmaker, attendee_count: int) -> int:
    async with factory() as session:
        players = [Player(steam_id=100 + index, display_name=str(index)) for index in range(5)]
        case = Match(match_id=8000 + attendee_count, parse_status="parsed")
        session.add_all([*players, case])
        await session.flush()
        session.add_all(
            MatchPlayer(
                match_id=case.id,
                player_id=player.id,
                hero_id=index,
                is_our_team=True,
            )
            for index, player in enumerate(players, start=1)
        )
        trial = Trial(match_id=case.id, status="waiting")
        session.add(trial)
        await session.flush()
        session.add_all(
            Attendance(trial_id=trial.id, player_id=player.id)
            for player in players[:attendee_count]
        )
        await session.commit()
        return trial.id


@pytest.mark.parametrize("attendee_count", [1, 2])
def test_start_vote_rejects_below_quorum(quorum_app, attendee_count: int) -> None:
    client, factory = quorum_app
    trial_id = asyncio.run(_seed_trial(factory, attendee_count))

    response = client.post(f"/api/trials/{trial_id}/start-vote")

    assert response.status_code == 409
    assert response.json()["detail"] == f"quorum not met: {attendee_count}/3"


def test_start_vote_accepts_three_attendees(quorum_app) -> None:
    client, factory = quorum_app
    trial_id = asyncio.run(_seed_trial(factory, 3))

    response = client.post(f"/api/trials/{trial_id}/start-vote")

    assert response.status_code == 200
    assert response.json()["type"] == "vote_start"


def test_trial_payload_exposes_quorum_contract(quorum_app) -> None:
    client, factory = quorum_app
    trial_id = asyncio.run(_seed_trial(factory, 2))

    payload = client.get(f"/api/trials/{trial_id}").json()

    assert payload["quorum"] == 3
    assert payload["attendee_count"] == 2
    assert payload["can_start"] is False


async def _seed_sparse_trial(
    factory: async_sessionmaker, registered: int, attendee_count: int
) -> int:
    """只有 `registered` 名我方选手被登记（其余 player_id 为 None），模拟路人/单排局。"""
    async with factory() as session:
        players = [
            Player(steam_id=900 + index, display_name=f"reg{index}")
            for index in range(registered)
        ]
        case = Match(match_id=9000 + registered * 10 + attendee_count, parse_status="parsed")
        session.add_all([*players, case])
        await session.flush()
        for index in range(5):
            session.add(
                MatchPlayer(
                    match_id=case.id,
                    player_id=players[index].id if index < registered else None,
                    hero_id=index,
                    is_our_team=True,
                )
            )
        trial = Trial(match_id=case.id, status="waiting")
        session.add(trial)
        await session.flush()
        session.add_all(
            Attendance(trial_id=trial.id, player_id=player.id)
            for player in players[:attendee_count]
        )
        await session.commit()
        return trial.id


@pytest.mark.parametrize("registered", [1, 2])
def test_start_vote_rejects_when_too_few_registered_players(
    quorum_app, registered: int
) -> None:
    """回归：登记候选不足 3 人时 quorum 曾被 min() 降到 1，导致一个人就能开庭。"""
    client, factory = quorum_app
    trial_id = asyncio.run(_seed_sparse_trial(factory, registered, registered))

    response = client.post(f"/api/trials/{trial_id}/start-vote")

    assert response.status_code == 409
    assert response.json()["detail"] == f"not enough registered players: {registered}/3"


def test_trial_payload_marks_sparse_match_ineligible(quorum_app) -> None:
    client, factory = quorum_app
    trial_id = asyncio.run(_seed_sparse_trial(factory, 1, 1))

    payload = client.get(f"/api/trials/{trial_id}").json()

    assert payload["quorum"] == 3
    assert payload["eligible"] is False
    assert payload["can_start"] is False


def test_single_vote_cannot_settle_sparse_trial(quorum_app) -> None:
    """回归：唯一到场者投一票即触发 _settle，判决自动完成。"""
    client, factory = quorum_app
    trial_id = asyncio.run(_seed_sparse_trial(factory, 1, 1))

    async def force_voting() -> tuple[int, int]:
        async with factory() as session:
            trial = await session.get(Trial, trial_id)
            trial.status = "voting"
            trial.vote_deadline = datetime.now(UTC) + timedelta(seconds=60)
            await session.commit()
            attendance = (
                await session.execute(
                    select(Attendance).where(Attendance.trial_id == trial_id)
                )
            ).scalars().first()
            return attendance.player_id, attendance.player_id

    voter_id, nominee_id = asyncio.run(force_voting())

    client.post(
        f"/api/trials/{trial_id}/vote",
        json={"voter_id": voter_id, "nominee_id": nominee_id},
    )

    payload = client.get(f"/api/trials/{trial_id}").json()
    assert payload["status"] != "closed", "单人投票不得自动结算"

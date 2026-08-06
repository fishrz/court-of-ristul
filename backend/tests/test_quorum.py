import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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

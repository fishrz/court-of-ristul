import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_session
from app.models import Match, MatchPlayer, Player, Trial, Vote
from app.routers import trials
from app.routers.trials import router as trials_router


@pytest.fixture
def trial_app(tmp_path) -> Iterator[tuple[TestClient, async_sessionmaker]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trials.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_session():
        async with factory() as session:
            yield session

    asyncio.run(prepare())
    app = FastAPI()
    app.include_router(trials_router)
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, factory
    asyncio.run(engine.dispose())


async def _seed_case(factory: async_sessionmaker, match_id: int = 9001) -> tuple[int, list[int]]:
    async with factory() as session:
        players = [
            Player(steam_id=100 + index, display_name=name)
            for index, name in enumerate(["A", "B", "C", "D", "E"])
        ]
        case = Match(
            match_id=match_id,
            parse_status="parsed",
            nominees_json=json.dumps(
                {
                    "suspects": [
                        {
                            "player": {"id": 101, "name": "B"},
                            "score": 9,
                            "evidence": [{"verdict": "AI 判 B", "severity": 3}],
                        },
                        {
                            "player": {"id": 100, "name": "A"},
                            "score": 4,
                            "evidence": [{"verdict": "AI 判 A", "severity": 2}],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
        )
        session.add_all([*players, case])
        await session.flush()
        session.add_all(
            [
                MatchPlayer(
                    match_id=case.id,
                    player_id=player.id,
                    hero_id=index + 1,
                    is_our_team=True,
                )
                for index, player in enumerate(players)
            ]
        )
        await session.commit()
        return case.match_id, [player.id for player in players]


def _open_and_attend(
    client: TestClient,
    factory: async_sessionmaker,
    attendee_count: int = 5,
    match_id: int = 9001,
) -> tuple[int, list[int]]:
    external_match_id, player_ids = asyncio.run(_seed_case(factory, match_id))
    opened = client.post(f"/api/trials/{external_match_id}/open")
    assert opened.status_code == 200
    trial_id = opened.json()["id"]
    for player_id in player_ids[:attendee_count]:
        response = client.post(
            f"/api/trials/{trial_id}/attend", json={"player_id": player_id}
        )
        assert response.status_code == 200
    return trial_id, player_ids


def test_open_persists_ai_verdict_and_duplicate_returns_409(trial_app) -> None:
    client, factory = trial_app
    match_id, player_ids = asyncio.run(_seed_case(factory))

    opened = client.post(f"/api/trials/{match_id}/open")
    duplicate = client.post(f"/api/trials/{match_id}/open")

    assert opened.status_code == 200
    assert opened.json()["ai_verdict_player_id"] == player_ids[1]
    assert opened.json()["ai_verdict"]["score"] == 9
    assert opened.json()["ai_verdict"]["evidence"][0]["verdict"] == "AI 判 B"
    assert duplicate.status_code == 409


def test_repeat_vote_updates_nominee_without_increasing_tally(trial_app) -> None:
    client, factory = trial_app
    trial_id, player_ids = _open_and_attend(client, factory)
    assert client.post(f"/api/trials/{trial_id}/start-vote").status_code == 200

    first = client.post(
        f"/api/trials/{trial_id}/vote",
        json={"voter_id": player_ids[0], "nominee_id": player_ids[0]},
    )
    changed = client.post(
        f"/api/trials/{trial_id}/vote",
        json={"voter_id": player_ids[0], "nominee_id": player_ids[2]},
    )
    state = client.get(f"/api/trials/{trial_id}").json()

    assert first.status_code == changed.status_code == 200
    assert sum(state["tally"].values()) == 1
    assert state["tally"] == {str(player_ids[2]): 1}

    async def stored_vote() -> tuple[int, int]:
        async with factory() as session:
            vote = await session.scalar(select(Vote).where(Vote.trial_id == trial_id))
            count = await session.scalar(
                select(func.count(Vote.id)).where(Vote.trial_id == trial_id)
            )
            return int(count or 0), vote.nominee_id

    assert asyncio.run(stored_vote()) == (1, player_ids[2])


def test_concurrent_repeat_vote_is_idempotent(trial_app, monkeypatch) -> None:
    client, factory = trial_app
    trial_id, player_ids = _open_and_attend(client, factory)
    assert client.post(f"/api/trials/{trial_id}/start-vote").status_code == 200

    original_get_trial = trials._get_trial
    both_loaded = asyncio.Event()
    loaded = 0

    async def synchronize_vote_loads(session, requested_trial_id):
        nonlocal loaded
        trial = await original_get_trial(session, requested_trial_id)
        loaded += 1
        if loaded == 2:
            both_loaded.set()
        await both_loaded.wait()
        return trial

    monkeypatch.setattr(trials, "_get_trial", synchronize_vote_loads)

    async def vote_twice() -> list[int]:
        async with AsyncClient(
            transport=ASGITransport(app=client.app), base_url="http://test"
        ) as async_client:
            responses = await asyncio.gather(
                async_client.post(
                    f"/api/trials/{trial_id}/vote",
                    json={"voter_id": player_ids[0], "nominee_id": player_ids[0]},
                ),
                async_client.post(
                    f"/api/trials/{trial_id}/vote",
                    json={"voter_id": player_ids[0], "nominee_id": player_ids[2]},
                ),
            )
            return [response.status_code for response in responses]

    assert asyncio.run(vote_twice()) == [200, 200]
    monkeypatch.setattr(trials, "_get_trial", original_get_trial)
    state = client.get(f"/api/trials/{trial_id}").json()
    assert sum(state["tally"].values()) == 1


def test_start_vote_is_idempotent_and_preserves_deadline(trial_app) -> None:
    client, factory = trial_app
    trial_id, _ = _open_and_attend(client, factory)

    first = client.post(f"/api/trials/{trial_id}/start-vote")
    second = client.post(f"/api/trials/{trial_id}/start-vote")

    assert first.status_code == second.status_code == 200
    assert second.json()["deadline"] == first.json()["deadline"]


def test_tie_uses_precomputed_ai_verdict_and_reports_disagreement(trial_app) -> None:
    client, factory = trial_app
    trial_id, player_ids = _open_and_attend(client, factory, attendee_count=4)
    client.post(f"/api/trials/{trial_id}/start-vote")

    nominees = [player_ids[0], player_ids[0], player_ids[2], player_ids[2]]
    for voter_id, nominee_id in zip(player_ids[:4], nominees, strict=True):
        response = client.post(
            f"/api/trials/{trial_id}/vote",
            json={"voter_id": voter_id, "nominee_id": nominee_id},
        )
        assert response.status_code == 200

    state = client.get(f"/api/trials/{trial_id}").json()
    assert state["status"] == "closed"
    assert state["verdict_player_id"] == state["ai_verdict_player_id"] == player_ids[1]
    assert state["ai_agrees"] is True
    assert sum(state["tally"].values()) <= 4


def test_player_verdict_and_ai_verdict_remain_independent(trial_app) -> None:
    client, factory = trial_app
    trial_id, player_ids = _open_and_attend(client, factory)
    client.post(f"/api/trials/{trial_id}/start-vote")

    for voter_id in player_ids:
        response = client.post(
            f"/api/trials/{trial_id}/vote",
            json={"voter_id": voter_id, "nominee_id": player_ids[0]},
        )
        assert response.status_code == 200

    state = client.get(f"/api/trials/{trial_id}").json()
    assert state["verdict_player_id"] == player_ids[0]
    assert state["ai_verdict_player_id"] == player_ids[1]
    assert state["ai_agrees"] is False
    assert sum(state["tally"].values()) == 5


def test_vote_after_server_deadline_is_rejected(trial_app) -> None:
    client, factory = trial_app
    trial_id, player_ids = _open_and_attend(client, factory)
    client.post(f"/api/trials/{trial_id}/start-vote")

    async def expire_vote() -> None:
        async with factory() as session:
            trial = await session.get(Trial, trial_id)
            trial.vote_deadline = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    asyncio.run(expire_vote())
    response = client.post(
        f"/api/trials/{trial_id}/vote",
        json={"voter_id": player_ids[0], "nominee_id": player_ids[0]},
    )
    assert response.status_code == 409


def test_appeal_rejects_more_than_60_characters(trial_app) -> None:
    client, factory = trial_app
    trial_id, _ = _open_and_attend(client, factory)

    response = client.post(
        f"/api/trials/{trial_id}/appeal", json={"text": "大" * 61}
    )

    assert response.status_code == 422


def test_websocket_receives_attend_vote_and_verdict_events(trial_app) -> None:
    client, factory = trial_app
    match_id, player_ids = asyncio.run(_seed_case(factory))
    trial_id = client.post(f"/api/trials/{match_id}/open").json()["id"]

    with client.websocket_connect(f"/ws/trials/{trial_id}") as websocket:
        for index, player_id in enumerate(player_ids, start=1):
            client.post(
                f"/api/trials/{trial_id}/attend", json={"player_id": player_id}
            )
            event = websocket.receive_json()
            assert event == {
                "type": "attend",
                "player_id": player_id,
                "here": index,
                "total": 5,
            }
        assert websocket.receive_json() == {"type": "stage", "stage": "evidence"}

        client.post(f"/api/trials/{trial_id}/start-vote")
        assert websocket.receive_json()["type"] == "vote_start"

        for index, voter_id in enumerate(player_ids):
            client.post(
                f"/api/trials/{trial_id}/vote",
                json={"voter_id": voter_id, "nominee_id": player_ids[0]},
            )
            vote_event = websocket.receive_json()
            assert vote_event["type"] == "vote"
            assert sum(vote_event["tally"].values()) == index + 1

        verdict = websocket.receive_json()
        assert verdict["type"] == "verdict"
        assert verdict["guilty_player_id"] == player_ids[0]
        assert verdict["ai_verdict_player_id"] == player_ids[1]
        assert verdict["ai_agrees"] is False

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


def test_open_persists_ai_verdict_and_duplicate_returns_same_trial(trial_app) -> None:
    client, factory = trial_app
    match_id, player_ids = asyncio.run(_seed_case(factory))

    opened = client.post(f"/api/trials/{match_id}/open")
    duplicate = client.post(f"/api/trials/{match_id}/open")

    assert opened.status_code == 200
    assert opened.json()["ai_verdict_player_id"] == player_ids[1]
    assert opened.json()["ai_verdict"]["score"] == 9
    assert opened.json()["ai_verdict"]["evidence"][0]["verdict"] == "AI 判 B"
    # 一局只有一场庭：重复开庭返回既有那场，让全员进同一个房间
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == opened.json()["id"]


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


def test_start_vote_rejects_before_all_players_attend(trial_app, monkeypatch) -> None:
    client, factory = trial_app
    trial_id, _ = _open_and_attend(client, factory, attendee_count=1)
    settlements = []
    broadcasts = []

    async def record_settlement(requested_trial_id, deadline):
        settlements.append((requested_trial_id, deadline))

    async def record_broadcast(requested_trial_id, event):
        broadcasts.append((requested_trial_id, event))

    monkeypatch.setattr(trials, "_settle_at_deadline", record_settlement)
    monkeypatch.setattr(trials.manager, "broadcast", record_broadcast)

    response = client.post(f"/api/trials/{trial_id}/start-vote")

    async def persisted_state() -> tuple[str, datetime | None]:
        async with factory() as session:
            trial = await session.get(Trial, trial_id)
            return trial.status, trial.vote_deadline

    persisted_state_value = asyncio.run(persisted_state())
    assert response.status_code == 409
    assert persisted_state_value == ("waiting", None)
    assert settlements == []
    assert broadcasts == []


def test_concurrent_start_vote_claims_once(trial_app, monkeypatch) -> None:
    client, factory = trial_app
    trial_id, _ = _open_and_attend(client, factory)

    original_get_trial = trials._get_trial
    both_loaded = asyncio.Event()
    loaded = 0
    settlements = []
    broadcasts = []

    async def synchronize_initial_loads(session, requested_trial_id):
        nonlocal loaded
        trial = await original_get_trial(session, requested_trial_id)
        loaded += 1
        if loaded <= 2:
            if loaded == 2:
                both_loaded.set()
            await both_loaded.wait()
        return trial

    async def record_settlement(requested_trial_id, deadline):
        settlements.append((requested_trial_id, deadline))

    async def record_broadcast(requested_trial_id, event):
        broadcasts.append((requested_trial_id, event))

    monkeypatch.setattr(trials, "_get_trial", synchronize_initial_loads)
    monkeypatch.setattr(trials, "_settle_at_deadline", record_settlement)
    monkeypatch.setattr(trials.manager, "broadcast", record_broadcast)

    async def start_twice():
        async with AsyncClient(
            transport=ASGITransport(app=client.app), base_url="http://test"
        ) as async_client:
            return await asyncio.gather(
                async_client.post(f"/api/trials/{trial_id}/start-vote"),
                async_client.post(f"/api/trials/{trial_id}/start-vote"),
            )

    responses = asyncio.run(start_twice())

    async def persisted_deadline() -> datetime | None:
        async with factory() as session:
            trial = await session.get(Trial, trial_id)
            return trial.vote_deadline

    persisted_deadline_value = asyncio.run(persisted_deadline())
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["deadline"] == responses[1].json()["deadline"]
    assert persisted_deadline_value is not None
    assert responses[0].json()["deadline"] == (
        persisted_deadline_value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    )
    assert len(settlements) == 1
    assert settlements[0] == (trial_id, persisted_deadline_value.replace(tzinfo=UTC))
    assert len(broadcasts) == 1
    assert broadcasts[0] == (trial_id, responses[0].json())


def test_tie_uses_precomputed_ai_verdict_and_reports_disagreement(trial_app) -> None:
    client, factory = trial_app
    trial_id, player_ids = _open_and_attend(client, factory)
    client.post(f"/api/trials/{trial_id}/start-vote")

    nominees = [
        player_ids[0],
        player_ids[0],
        player_ids[2],
        player_ids[2],
        player_ids[4],
    ]
    for voter_id, nominee_id in zip(player_ids, nominees, strict=True):
        response = client.post(
            f"/api/trials/{trial_id}/vote",
            json={"voter_id": voter_id, "nominee_id": nominee_id},
        )
        assert response.status_code == 200

    state = client.get(f"/api/trials/{trial_id}").json()
    assert state["status"] == "closed"
    assert state["verdict_player_id"] == state["ai_verdict_player_id"] == player_ids[1]
    assert state["ai_agrees"] is True
    assert sum(state["tally"].values()) == 5


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


def _force_deadline_past(factory: async_sessionmaker, trial_id: int) -> None:
    """把 deadline 推到过去，模拟投票超时但定时器已丢失（服务重启）。"""

    async def run() -> None:
        async with factory() as session:
            trial = await session.get(Trial, trial_id)
            trial.vote_deadline = datetime.now(UTC) - timedelta(seconds=5)
            await session.commit()

    asyncio.run(run())


def test_reading_overdue_trial_settles_it(trial_app) -> None:
    """定时器丢失后，任何人读取状态都应触发结算，而不是永远停在 voting。"""
    client, factory = trial_app
    trial_id, player_ids = _open_and_attend(client, factory)
    assert client.post(f"/api/trials/{trial_id}/start-vote").status_code == 200
    client.post(
        f"/api/trials/{trial_id}/vote",
        json={"voter_id": player_ids[0], "nominee_id": player_ids[1]},
    )

    _force_deadline_past(factory, trial_id)

    state = client.get(f"/api/trials/{trial_id}").json()
    assert state["status"] == "closed"
    assert state["verdict"]["guilty_player_id"] == player_ids[1]


def test_startup_settles_overdue_trials(trial_app) -> None:
    """重启补结算：settle_overdue_trials 应结案已超时的审判。"""
    client, factory = trial_app
    trial_id, player_ids = _open_and_attend(client, factory)
    assert client.post(f"/api/trials/{trial_id}/start-vote").status_code == 200
    client.post(
        f"/api/trials/{trial_id}/vote",
        json={"voter_id": player_ids[0], "nominee_id": player_ids[1]},
    )
    _force_deadline_past(factory, trial_id)

    async def run() -> int:
        original = trials.SessionLocal
        trials.SessionLocal = factory
        try:
            return await trials.settle_overdue_trials()
        finally:
            trials.SessionLocal = original

    settled = asyncio.run(run())
    assert settled == 1

    async def status() -> str:
        async with factory() as session:
            trial = await session.get(Trial, trial_id)
            return trial.status

    assert asyncio.run(status()) == "closed"


def test_stale_timer_does_not_settle_a_different_trial(trial_app) -> None:
    """陈旧定时器（deadline 与当前记录不符）不得结算审判。"""
    client, factory = trial_app
    trial_id, _player_ids = _open_and_attend(client, factory)
    assert client.post(f"/api/trials/{trial_id}/start-vote").status_code == 200

    async def run() -> str:
        original = trials.SessionLocal
        trials.SessionLocal = factory
        try:
            # 用一个与数据库中不一致的 deadline 调用，模拟上一局遗留的任务
            stale = datetime.now(UTC) - timedelta(hours=1)
            await trials._settle_at_deadline(trial_id, stale)
        finally:
            trials.SessionLocal = original
        async with factory() as session:
            trial = await session.get(Trial, trial_id)
            return trial.status

    assert asyncio.run(run()) == "voting"


def test_trial_state_exposes_vote_detail(trial_app) -> None:
    """状态里要有投票明细，否则客户端重连后无法还原'我投过谁'。"""
    client, factory = trial_app
    trial_id, player_ids = _open_and_attend(client, factory)
    assert client.post(f"/api/trials/{trial_id}/start-vote").status_code == 200
    client.post(
        f"/api/trials/{trial_id}/vote",
        json={"voter_id": player_ids[0], "nominee_id": player_ids[1]},
    )

    state = client.get(f"/api/trials/{trial_id}").json()
    assert state["votes"] == [
        {"voter_id": player_ids[0], "nominee_id": player_ids[1]}
    ]
    assert state["tally"] == {str(player_ids[1]): 1}



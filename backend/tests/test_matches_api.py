import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import app
from app.models import Match, MatchPlayer, Player, Trial


@pytest_asyncio.fixture
async def api_client(session: AsyncSession):
    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_match_archive_filters_and_sorts(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    session.add_all(
        [
            Match(
                match_id=2,
                started_at=datetime(2026, 7, 2, tzinfo=UTC),
                we_won=False,
                parse_status="parsed",
            ),
            Match(
                match_id=1,
                started_at=datetime(2026, 7, 1, tzinfo=UTC),
                we_won=True,
                parse_status="parsed",
            ),
            Match(
                match_id=3,
                started_at=datetime(2026, 7, 3, tzinfo=UTC),
                parse_status="parsing",
            ),
        ]
    )
    await session.commit()

    all_cases = await api_client.get("/api/matches")
    losses = await api_client.get("/api/matches?filter=lose")
    pending = await api_client.get("/api/matches?filter=pending")
    unsupported = await api_client.get("/api/matches?filter=me")

    assert [case["match_id"] for case in all_cases.json()] == [3, 2, 1]
    assert [case["match_id"] for case in losses.json()] == [2]
    assert [case["match_id"] for case in pending.json()] == [3]
    assert unsupported.status_code == 422


@pytest.mark.asyncio
async def test_match_detail_includes_players_and_attribution(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    player = Player(steam_id=123, display_name="风希")
    case = Match(
        match_id=8917764448,
        parse_status="parsed",
        evidence_json=json.dumps({"123": [{"id": "x"}]}),
        nominees_json=json.dumps({"suspects": []}),
    )
    session.add_all([player, case])
    await session.flush()
    session.add(
        MatchPlayer(
            match_id=case.id,
            player_id=player.id,
            hero_id=25,
            is_our_team=True,
        )
    )
    await session.commit()

    response = await api_client.get("/api/matches/8917764448")

    assert response.status_code == 200
    assert response.json()["evidence"] == {"123": [{"id": "x"}]}
    assert response.json()["nominees"] == {"suspects": []}
    assert response.json()["players"][0]["player_id"] == player.id


@pytest.mark.asyncio
async def test_monthly_stats_are_self_consistent(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    guilty_a = Player(steam_id=1, display_name="A")
    guilty_b = Player(steam_id=2, display_name="B")
    session.add_all([guilty_a, guilty_b])
    await session.flush()
    cases = [
        Match(match_id=index, parse_status="parsed", we_won=(index == 1))
        for index in range(1, 5)
    ]
    session.add_all(cases)
    await session.flush()
    session.add_all(
        [
            Trial(
                match_id=cases[0].id,
                status="closed",
                closed_at=datetime.now(UTC),
            ),
            Trial(
                match_id=cases[1].id,
                status="closed",
                verdict_player_id=guilty_a.id,
                closed_at=datetime.now(UTC),
            ),
            Trial(
                match_id=cases[2].id,
                status="closed",
                verdict_player_id=guilty_a.id,
                closed_at=datetime.now(UTC),
            ),
            Trial(
                match_id=cases[3].id,
                status="closed",
                verdict_player_id=guilty_b.id,
                closed_at=datetime.now(UTC),
            ),
        ]
    )
    await session.commit()

    response = await api_client.get("/api/stats/monthly")
    stats = response.json()

    assert response.status_code == 200
    assert stats["trials"] == stats["wins"] + stats["guilty"]
    assert sum(item["count"] for item in stats["leaderboard"]) == stats["guilty"]
    assert stats == {
        "trials": 4,
        "wins": 1,
        "guilty": 3,
        "leaderboard": [
            {"player_id": guilty_a.id, "display_name": "A", "count": 2},
            {"player_id": guilty_b.id, "display_name": "B", "count": 1},
        ],
    }

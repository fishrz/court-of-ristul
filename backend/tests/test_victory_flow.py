from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import poller
from app.db import get_session
from app.main import app
from app.models import Match, Player, Trial


@pytest_asyncio.fixture
async def api_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_monthly_stats_split_loss_verdicts_from_victory_mvps(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    loser = Player(steam_id=1, display_name="大锅")
    mvp = Player(steam_id=2, display_name="MVP")
    win = Match(match_id=1, parse_status="parsed", we_won=True)
    loss = Match(match_id=2, parse_status="parsed", we_won=False)
    session.add_all([loser, mvp, win, loss])
    await session.flush()
    session.add_all(
        [
            Trial(
                match_id=win.id,
                status="closed",
                verdict_player_id=mvp.id,
                closed_at=datetime.now(UTC),
            ),
            Trial(
                match_id=loss.id,
                status="closed",
                verdict_player_id=loser.id,
                closed_at=datetime.now(UTC),
            ),
        ]
    )
    await session.commit()

    stats = (await api_client.get("/api/stats/monthly")).json()

    assert stats["trials"] == 2
    assert stats["wins"] == 1
    assert stats["guilty"] == 1
    assert stats["leaderboard"] == [
        {"player_id": loser.id, "display_name": "大锅", "count": 1}
    ]
    assert stats["mvp_leaderboard"] == [
        {"player_id": mvp.id, "display_name": "MVP", "count": 1}
    ]


@pytest.mark.asyncio
async def test_match_list_exposes_trial_route_contract(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    case = Match(match_id=3, parse_status="parsed", we_won=True)
    session.add(case)
    await session.flush()
    trial = Trial(match_id=case.id, status="waiting")
    session.add(trial)
    await session.commit()

    item = (await api_client.get("/api/matches")).json()[0]

    assert item["trial_id"] == trial.id
    assert item["trial_status"] == "waiting"


@pytest.mark.asyncio
async def test_player_picker_lists_only_active_public_fields(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    active = Player(steam_id=11, display_name="在册", avatar_url="https://example/a.png")
    inactive = Player(steam_id=12, display_name="停用", is_active=False)
    session.add_all([active, inactive])
    await session.commit()

    payload = (await api_client.get("/api/players")).json()

    assert payload == [
        {"id": active.id, "display_name": "在册", "avatar_url": "https://example/a.png"}
    ]


@pytest.mark.asyncio
async def test_victory_parsing_requests_praise_evidence_and_merit_scoring(
    session: AsyncSession, monkeypatch
) -> None:
    players = [Player(steam_id=100 + index, display_name=str(index)) for index in range(5)]
    case = Match(match_id=44, parse_status="parsing", our_side="radiant")
    session.add_all([*players, case])
    await session.flush()
    captured = {}

    def fake_accuse(_db, _team, **options):
        captured.update(options)
        return {"suspects": []}

    monkeypatch.setattr(poller, "accuse", fake_accuse)
    monkeypatch.setattr(poller, "_load_meme_db", lambda: {"metrics": {}, "entries": []})
    raw_players = [
        {
            "account_id": player.steam_id,
            "player_slot": index,
            "hero_id": index + 1,
            "net_worth": 100,
            "hero_damage": 100,
        }
        for index, player in enumerate(players)
    ] + [
        {"account_id": 200 + index, "player_slot": 128 + index, "hero_id": 10 + index}
        for index in range(5)
    ]

    await poller._store_parsed_match(
        session,
        case,
        {"players": raw_players, "radiant_win": True, "duration": 1200},
        {player.steam_id: player for player in players},
    )

    assert captured["contexts"] == ["victory"]
    assert captured["tones"] == {"praise", "fact"}
    assert captured["mode"] == "safe"
    assert captured["score_mode"] == "merit"
    assert case.we_won is True

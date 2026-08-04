from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import app
from app.models import Match, MatchPlayer, Player


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


async def _seed(session: AsyncSession, *, matches: int = 4) -> Player:
    """造一个玩家 + 若干局。全部同英雄，方便断言样本门槛。"""
    player = Player(steam_id=114468354, display_name="WhatToSay")
    session.add(player)
    await session.flush()

    for index in range(matches):
        match = Match(
            match_id=9000000000 + index,
            started_at=datetime(2026, 7, 20 + index, 12, 0, tzinfo=UTC),
            duration=2400,
            we_won=index % 2 == 0,
            our_side="radiant",
        )
        session.add(match)
        await session.flush()
        session.add(
            MatchPlayer(
                match_id=match.id,
                player_id=player.id,
                hero_id=42,
                hero_name="冥魂大帝",
                is_our_team=True,
                kills=5,
                deaths=3,
                assists=9,
            )
        )
    await session.commit()
    return player


@pytest.mark.asyncio
async def test_dossier_returns_record_and_recent(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    player = await _seed(session, matches=4)

    response = await api_client.get(f"/api/dossier/{player.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["player"]["display_name"] == "WhatToSay"
    assert body["record"]["matches"] == 4
    assert body["record"]["wins"] == 2
    assert body["record"]["convictions"] == 0
    # 最近比赛必须回传，卷宗页靠它列出可点开的局
    assert len(body["recent"]) == 4
    assert body["recent"][0]["hero_name"] == "冥魂大帝"


@pytest.mark.asyncio
async def test_dossier_hides_winrate_when_sample_too_small(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    """1 场 100% 是噪音。样本不足必须给 null，不能让前端显示成强势英雄。"""
    player = await _seed(session, matches=1)

    body = (await api_client.get(f"/api/dossier/{player.id}")).json()

    hero = body["heroes"][0]
    assert hero["matches"] == 1
    assert hero["enough_sample"] is False
    assert hero["winrate"] is None


@pytest.mark.asyncio
async def test_dossier_trend_gated_by_match_count(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    player = await _seed(session, matches=1)

    body = (await api_client.get(f"/api/dossier/{player.id}")).json()

    assert body["trend_available"] is False
    assert body["trend"] == []


@pytest.mark.asyncio
async def test_dossier_unknown_player_404(api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/dossier/9999")).status_code == 404


@pytest.mark.asyncio
async def test_match_dossier_unknown_match_404(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    player = await _seed(session, matches=1)

    response = await api_client.get(f"/api/dossier/{player.id}/match/1234")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_match_dossier_unparsed_degrades(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    """没有 raw_json 就没有事实层。必须明说未解析，不能编 findings。"""
    player = await _seed(session, matches=1)

    body = (
        await api_client.get(f"/api/dossier/{player.id}/match/9000000000")
    ).json()

    assert body["parsed"] is False
    assert body.get("findings", []) == []


@pytest.mark.asyncio
async def test_coach_without_key_returns_unavailable(
    api_client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配 key 时教练要安静降级成 200，不是 500——前端不该显示故障。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    player = await _seed(session, matches=1)

    response = await api_client.post(
        f"/api/dossier/{player.id}/match/9000000000/coach"
    )

    assert response.status_code == 200
    assert response.json()["available"] is False

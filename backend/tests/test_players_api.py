from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import app
from app.models import Player

STEAM_ID64_OFFSET = 76561197960265728


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
async def test_create_player_converts_steam_id64(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    account_id = 123456789
    response = await api_client.post(
        "/api/players",
        json={
            "steam_id": STEAM_ID64_OFFSET + account_id,
            "display_name": "风希",
        },
    )

    assert response.status_code == 201
    assert response.json()["steam_id"] == account_id
    stored = await session.get(Player, response.json()["id"])
    assert stored is not None
    assert stored.steam_id == account_id


@pytest.mark.asyncio
async def test_list_and_soft_delete_players(
    api_client: AsyncClient, session: AsyncSession
) -> None:
    player = Player(steam_id=123456789, display_name="风希")
    session.add(player)
    await session.commit()

    listed = await api_client.get("/api/players")
    deleted = await api_client.delete(f"/api/players/{player.id}")
    await session.refresh(player)

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [player.id]
    assert deleted.status_code == 204
    assert player.is_active is False


@pytest.mark.asyncio
async def test_rejects_invalid_steam_id32(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/players", json={"steam_id": STEAM_ID64_OFFSET - 1, "display_name": "x"}
    )

    assert response.status_code == 422

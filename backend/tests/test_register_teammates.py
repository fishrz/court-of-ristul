import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Match, MatchPlayer, Player
from scripts.register_teammates import OWNER_ACCOUNT_ID, register_teammates


def raw_match(*players: dict) -> str:
    return json.dumps({"players": list(players)}, ensure_ascii=False)


def participant(
    account_id: int,
    hero_id: int,
    *,
    radiant: bool = True,
    name: str | None = None,
) -> dict:
    return {
        "account_id": account_id,
        "hero_id": hero_id,
        "player_slot": hero_id if radiant else 128 + hero_id,
        "personaname": name,
    }


async def add_match(
    session: AsyncSession,
    match_id: int,
    *players: dict,
) -> Match:
    match = Match(
        match_id=match_id,
        our_side="radiant",
        parse_status="parsed",
        raw_json=raw_match(*players),
    )
    session.add(match)
    await session.flush()
    session.add_all(
        MatchPlayer(
            match_id=match.id,
            hero_id=player["hero_id"],
            is_our_team=player["player_slot"] < 128,
        )
        for player in players
    )
    return match


@pytest.mark.asyncio
async def test_registers_only_teammates_meeting_threshold_and_uses_common_name(
    session: AsyncSession,
) -> None:
    for index, name in enumerate(["旧名", "常用名", "常用名"], start=1):
        players = [
            participant(OWNER_ACCOUNT_ID, 1),
            participant(200, 2, name=name),
            participant(400, 4, radiant=False, name="敌人"),
        ]
        if index < 3:
            players.append(participant(300, 3, name="路人"))
        await add_match(
            session,
            index,
            *players,
        )
    await session.commit()

    result = await register_teammates(session, apply=True)

    players = list(await session.scalars(select(Player).order_by(Player.steam_id)))
    assert result == {"created": 1, "linked": 3}
    assert [(player.steam_id, player.display_name) for player in players] == [
        (200, "常用名")
    ]


@pytest.mark.asyncio
async def test_register_is_idempotent_and_links_matching_match_players(
    session: AsyncSession,
) -> None:
    existing = Player(steam_id=200, display_name="已登记")
    session.add(existing)
    for index in range(3):
        await add_match(
            session,
            100 + index,
            participant(OWNER_ACCOUNT_ID, 10),
            participant(200, 20, name="新昵称"),
        )
    await session.commit()

    first = await register_teammates(session, apply=True)
    second = await register_teammates(session, apply=True)

    count = await session.scalar(select(func.count()).select_from(Player))
    linked_ids = list(
        await session.scalars(
            select(MatchPlayer.player_id)
            .where(MatchPlayer.hero_id == 20)
            .order_by(MatchPlayer.id)
        )
    )
    assert first == {"created": 0, "linked": 3}
    assert second == {"created": 0, "linked": 0}
    assert count == 1
    assert linked_ids == [existing.id, existing.id, existing.id]


@pytest.mark.asyncio
async def test_dry_run_reports_without_writing(session: AsyncSession) -> None:
    for index in range(3):
        await add_match(
            session,
            200 + index,
            participant(OWNER_ACCOUNT_ID, 30),
            participant(500, 40, name="待登记"),
        )
    await session.commit()

    result = await register_teammates(session, apply=False)

    player_count = await session.scalar(select(func.count()).select_from(Player))
    linked_count = await session.scalar(
        select(func.count()).select_from(MatchPlayer).where(MatchPlayer.player_id.is_not(None))
    )
    assert result == {"created": 1, "linked": 3}
    assert player_count == 0
    assert linked_count == 0

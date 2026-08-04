from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Base
from app.models import Attendance, Match, Player, Trial, Vote


@pytest.mark.asyncio
async def test_creates_all_tables(session: AsyncSession) -> None:
    connection = await session.connection()
    table_names = await connection.run_sync(
        lambda sync_connection: set(inspect(sync_connection).get_table_names())
    )

    assert set(Base.metadata.tables) == {
        "players",
        "matches",
        "match_players",
        "trials",
        "attendances",
        "votes",
        "meta_snapshots",
    }
    assert table_names == set(Base.metadata.tables)


@pytest.mark.asyncio
async def test_duplicate_match_id_raises_integrity_error(session: AsyncSession) -> None:
    session.add_all(
        [
            Match(match_id=8917764448, parse_status="pending"),
            Match(match_id=8917764448, parse_status="pending"),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_duplicate_vote_raises_integrity_error(session: AsyncSession) -> None:
    voter = Player(steam_id=123456789, display_name="voter")
    nominee = Player(steam_id=987654321, display_name="nominee")
    match = Match(match_id=8917764448, parse_status="parsed")
    session.add_all([voter, nominee, match])
    await session.flush()
    trial = Trial(match_id=match.id, status="voting", created_at=datetime.now(UTC))
    session.add(trial)
    await session.flush()
    session.add_all(
        [
            Vote(trial_id=trial.id, voter_id=voter.id, nominee_id=nominee.id),
            Vote(trial_id=trial.id, voter_id=voter.id, nominee_id=voter.id),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_duplicate_trial_match_id_raises_integrity_error(
    session: AsyncSession,
) -> None:
    match = Match(match_id=8917764448, parse_status="parsed")
    session.add(match)
    await session.flush()
    session.add_all(
        [
            Trial(match_id=match.id, status="waiting"),
            Trial(match_id=match.id, status="waiting"),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_duplicate_attendance_raises_integrity_error(
    session: AsyncSession,
) -> None:
    player = Player(steam_id=123456789, display_name="player")
    match = Match(match_id=8917764448, parse_status="parsed")
    session.add_all([player, match])
    await session.flush()
    trial = Trial(match_id=match.id, status="waiting")
    session.add(trial)
    await session.flush()
    session.add_all(
        [
            Attendance(trial_id=trial.id, player_id=player.id),
            Attendance(trial_id=trial.id, player_id=player.id),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Match, MatchPlayer, Player
from app.poller import poll_once


class FakeOpenDotaClient:
    def __init__(
        self,
        recent_by_player: dict[int, list[dict]],
        parsed_matches: dict[int, dict] | None = None,
    ) -> None:
        self.recent_by_player = recent_by_player
        self.parsed_matches = parsed_matches or {}
        self.parse_requests: list[int] = []

    async def get_recent_matches(self, steam_id: int) -> list[dict]:
        return self.recent_by_player.get(steam_id, [])

    async def request_parse(self, match_id: int) -> dict:
        self.parse_requests.append(match_id)
        return {"job": {"jobId": 1}}

    async def get_match(self, match_id: int) -> dict | None:
        return self.parsed_matches.get(match_id)


async def seed_players(session: AsyncSession, count: int = 5) -> list[Player]:
    players = [
        Player(steam_id=1000 + index, display_name=f"player-{index}")
        for index in range(count)
    ]
    session.add_all(players)
    await session.commit()
    return players


def recent_match(match_id: int, player_slot: int = 0) -> dict:
    return {
        "match_id": match_id,
        "player_slot": player_slot,
        "radiant_win": True,
        "start_time": 1785370000,
        "duration": 2400,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("member_count, expected_cases", [(3, 0), (4, 1), (5, 1)])
async def test_poll_only_creates_cases_for_at_least_four_teammates(
    session: AsyncSession, member_count: int, expected_cases: int
) -> None:
    players = await seed_players(session)
    client = FakeOpenDotaClient(
        {
            player.steam_id: [recent_match(8917764448)]
            for player in players[:member_count]
        }
    )

    await poll_once(session, client)

    count = await session.scalar(select(func.count()).select_from(Match))
    assert count == expected_cases
    assert client.parse_requests == ([8917764448] if expected_cases else [])


@pytest.mark.asyncio
async def test_polling_same_match_twice_creates_one_case(session: AsyncSession) -> None:
    players = await seed_players(session)
    client = FakeOpenDotaClient(
        {player.steam_id: [recent_match(8917764448)] for player in players}
    )

    await poll_once(session, client)
    await poll_once(session, client)

    count = await session.scalar(select(func.count()).select_from(Match))
    assert count == 1
    assert client.parse_requests == [8917764448]


@pytest.mark.asyncio
async def test_poll_stores_parsed_metrics_and_attribution(session: AsyncSession) -> None:
    players = await seed_players(session)
    parsed_players = []
    for index, player in enumerate(players):
        parsed_players.append(
            {
                "account_id": player.steam_id,
                "player_slot": index,
                "hero_id": index + 1,
                "personaname": player.display_name,
                "kills": 2,
                "deaths": 3,
                "assists": 4,
                "gold_per_min": 400,
                "xp_per_min": 500,
                "net_worth": 10000,
                "hero_damage": 10000,
                "tower_damage": 500,
                "lh_t": [0] * 9 + [40],
                "lane_role": 1 if index == 0 else 4,
                "teamfight_participation": 0.6,
                "obs_placed": 2,
                "sen_placed": 1,
                "item_uses": {"tpscroll": 8},
                "buyback_count": 0,
                "stuns": 5.0,
                "camps_stacked": 1,
                "dn_at_10": 2,
                "xp_at_10": 3000,
            }
        )
    parsed_players.extend(
        {"player_slot": 128 + index, "hero_id": index + 10}
        for index in range(5)
    )
    client = FakeOpenDotaClient(
        {player.steam_id: [recent_match(8917764448)] for player in players},
        {
            8917764448: {
                "match_id": 8917764448,
                "version": 21,
                "start_time": 1785370000,
                "duration": 2400,
                "radiant_win": False,
                "players": parsed_players,
            }
        },
    )

    await poll_once(session, client)

    case = await session.scalar(select(Match).where(Match.match_id == 8917764448))
    stored_players = list(
        await session.scalars(select(MatchPlayer).where(MatchPlayer.match_id == case.id))
    )
    assert case.parse_status == "parsed"
    assert case.evidence_json is not None
    assert case.nominees_json is not None
    assert len(stored_players) == 10
    assert stored_players[0].lh_at_10 == 40
    assert stored_players[0].tp_uses == 8

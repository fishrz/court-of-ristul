"""scripts/add_match.py 的补录行为。

补录脚本是漏局的唯一补救手段：poller 只看 OpenDota 最近 20 局的窗口，
滑出窗口的比赛永远回不来。所以这里重点验的是「不会重复建案」「未解析
也要先入库并请求 parse」「已解析要走和 poller 完全相同的入库路径」。
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Match, MatchPlayer, Player
from scripts.add_match import add_one


class FakeClient:
    def __init__(self, matches: dict[int, dict | None]) -> None:
        self.matches = matches
        self.parse_requests: list[int] = []

    async def get_match(self, match_id: int) -> dict | None:
        return self.matches.get(match_id)

    async def request_parse(self, match_id: int) -> dict:
        self.parse_requests.append(match_id)
        return {"job": {"jobId": 1}}


async def seed_players(session: AsyncSession, count: int = 5) -> list[Player]:
    players = [
        Player(steam_id=1000 + index, display_name=f"player-{index}")
        for index in range(count)
    ]
    session.add_all(players)
    await session.commit()
    return players


def unparsed_match(players: list[Player], *, our_slot: int = 0) -> dict:
    """OpenDota 还没 parse 的返回：有 players 名单，但没有 version/teamfights。"""
    return {
        "match_id": 8928973355,
        "start_time": 1785370000,
        "duration": 2220,
        "radiant_win": True,
        "players": [
            {"account_id": player.steam_id, "player_slot": our_slot + index}
            for index, player in enumerate(players)
        ],
    }


def parsed_match(players: list[Player]) -> dict:
    ours = [
        {
            "account_id": player.steam_id,
            "player_slot": index,
            "hero_id": index + 1,
            "personaname": player.display_name,
            "kills": 1,
            "deaths": 10,
            "assists": 5,
            "gold_per_min": 300,
            "xp_per_min": 400,
            "net_worth": 8000,
            "hero_damage": 9000,
            "tower_damage": 200,
            "lh_t": [0] * 9 + [30],
            "lane_role": 1 if index == 0 else 4,
            "teamfight_participation": 0.5,
            "obs_placed": 1,
            "sen_placed": 1,
            "item_uses": {"tpscroll": 4},
            "buyback_count": 0,
            "stuns": 2.0,
            "camps_stacked": 0,
            "dn_at_10": 1,
            "xp_at_10": 2500,
        }
        for index, player in enumerate(players)
    ]
    ours.extend(
        {"player_slot": 128 + index, "hero_id": index + 10} for index in range(5)
    )
    return {
        "match_id": 8929041534,
        "version": 21,
        "start_time": 1785380000,
        "duration": 1080,
        "radiant_win": True,
        "players": ours,
    }


@pytest.mark.asyncio
async def test_dry_run_does_not_write(session: AsyncSession) -> None:
    players = await seed_players(session)
    client = FakeClient({8928973355: unparsed_match(players)})

    assert await add_one(session, client, 8928973355, apply=False) is True

    assert await session.scalar(select(func.count()).select_from(Match)) == 0
    assert client.parse_requests == []


@pytest.mark.asyncio
async def test_unparsed_match_is_stored_and_parse_requested(
    session: AsyncSession,
) -> None:
    """未解析也要先入库。等它滑出 recent 窗口就再也捞不回来了。"""
    players = await seed_players(session)
    client = FakeClient({8928973355: unparsed_match(players)})

    assert await add_one(session, client, 8928973355, apply=True) is True

    case = await session.scalar(select(Match).where(Match.match_id == 8928973355))
    assert case is not None
    assert case.parse_status == "parsing"
    assert client.parse_requests == [8928973355]


@pytest.mark.asyncio
async def test_existing_match_is_not_duplicated(session: AsyncSession) -> None:
    players = await seed_players(session)
    client = FakeClient({8928973355: unparsed_match(players)})

    await add_one(session, client, 8928973355, apply=True)
    assert await add_one(session, client, 8928973355, apply=True) is False

    assert await session.scalar(select(func.count()).select_from(Match)) == 1


@pytest.mark.asyncio
async def test_match_without_registered_player_is_rejected(
    session: AsyncSession,
) -> None:
    """一个登记玩家都没有就无从判断哪边是「我们」，宁可拒绝也不能瞎猜阵营。"""
    await seed_players(session)
    client = FakeClient(
        {
            8928973355: {
                "match_id": 8928973355,
                "radiant_win": True,
                "players": [{"account_id": 999999, "player_slot": 0}],
            }
        }
    )

    assert await add_one(session, client, 8928973355, apply=True) is False
    assert await session.scalar(select(func.count()).select_from(Match)) == 0


@pytest.mark.asyncio
async def test_missing_match_is_rejected(session: AsyncSession) -> None:
    await seed_players(session)
    client = FakeClient({8928973355: None})

    assert await add_one(session, client, 8928973355, apply=False) is False


@pytest.mark.asyncio
async def test_our_side_follows_registered_player_slot(session: AsyncSession) -> None:
    """两局漏局都是夜魇负；阵营认错会让胜负和归因整个翻过来。"""
    players = await seed_players(session)
    client = FakeClient({8928973355: unparsed_match(players, our_slot=128)})

    await add_one(session, client, 8928973355, apply=True)

    case = await session.scalar(select(Match).where(Match.match_id == 8928973355))
    assert case.our_side == "dire"
    assert case.we_won is False  # radiant_win=True，我们在夜魇


@pytest.mark.asyncio
async def test_parsed_match_stores_full_detail(session: AsyncSession) -> None:
    """已解析的局要走 poller 同一个入口，位置/指标/归因不能因为是补录就缺。"""
    players = await seed_players(session)
    client = FakeClient({8929041534: parsed_match(players)})

    await add_one(session, client, 8929041534, apply=True)

    case = await session.scalar(select(Match).where(Match.match_id == 8929041534))
    stored = list(
        await session.scalars(select(MatchPlayer).where(MatchPlayer.match_id == case.id))
    )
    assert case.parse_status == "parsed"
    assert case.evidence_json is not None
    assert len(stored) == 10
    assert client.parse_requests == []  # 已解析就不用再请求

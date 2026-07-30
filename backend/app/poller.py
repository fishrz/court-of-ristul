import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine import accuse
from app.models import Match, MatchPlayer, Player
from app.opendota import OpenDotaClient

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 300

# 一局要成为案卷，需要满足下面任意一条：
#   1. 至少 MIN_REGISTERED 个已登记玩家出现在同一队（队友都登记了的理想情况）；
#   2. 至少一个已登记玩家，且 OpenDota 报告的开黑人数 >= MIN_PARTY_SIZE。
# 第 2 条是给「只有一个人登记」的冷启动阶段用的：没有它，队友没登记完之前
# 一局都抓不到。party_size 由 Dota 自己给出，比猜要准。
MIN_REGISTERED = 3
MIN_PARTY_SIZE = 3


def _qualifies(members: Mapping[int, Mapping[str, Any]]) -> bool:
    if len(members) >= MIN_REGISTERED:
        return True
    return any(
        (recent.get("party_size") or 0) >= MIN_PARTY_SIZE for recent in members.values()
    )


class PollClient(Protocol):
    async def get_recent_matches(self, steam_id: int) -> Any | None: ...

    async def get_match(self, match_id: int) -> Any | None: ...

    async def request_parse(self, match_id: int) -> Any | None: ...


def _is_radiant(player_slot: int) -> bool:
    return player_slot < 128


async def poll_once(session: AsyncSession, client: PollClient) -> int:
    active_players = list(
        await session.scalars(select(Player).where(Player.is_active.is_(True)))
    )
    candidates: dict[tuple[int, bool], dict[int, Mapping[str, Any]]] = defaultdict(dict)
    for player in active_players:
        recent_matches = await client.get_recent_matches(player.steam_id)
        if not isinstance(recent_matches, list):
            continue
        for recent in recent_matches:
            if not isinstance(recent, Mapping) or recent.get("match_id") is None:
                continue
            key = (int(recent["match_id"]), _is_radiant(int(recent.get("player_slot", 0))))
            candidates[key][player.id] = recent

    created = 0
    for (match_id, radiant_side), members in candidates.items():
        if not _qualifies(members):
            continue
        if await session.scalar(select(Match.id).where(Match.match_id == match_id)):
            continue
        sample = next(iter(members.values()))
        radiant_win = sample.get("radiant_win")
        case = Match(
            match_id=match_id,
            started_at=_timestamp(sample.get("start_time")),
            duration=sample.get("duration"),
            radiant_win=radiant_win,
            our_side="radiant" if radiant_side else "dire",
            we_won=(radiant_win == radiant_side) if radiant_win is not None else None,
            parse_status="parsing",
        )
        session.add(case)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            continue
        await client.request_parse(match_id)
        created += 1

    parsing = list(
        await session.scalars(select(Match).where(Match.parse_status == "parsing"))
    )
    player_by_steam_id = {player.steam_id: player for player in active_players}
    for case in parsing:
        data = await client.get_match(case.match_id)
        if _match_is_parsed(data):
            await _store_parsed_match(session, case, data, player_by_steam_id)
    return created


def _timestamp(value: Any) -> Any:
    if value is None:
        return None
    from datetime import UTC, datetime

    return datetime.fromtimestamp(int(value), UTC)


def _match_is_parsed(data: Any) -> bool:
    return isinstance(data, Mapping) and isinstance(data.get("players"), list) and bool(
        data.get("version") or data.get("objectives") or data.get("teamfights")
    )


def _lane_role(player: Mapping[str, Any]) -> str | None:
    lane = player.get("lane_role")
    if player.get("is_roaming") or lane == 4:
        return "support"
    return {1: "carry", 2: "mid", 3: "offlane"}.get(lane)


def _metric_player(
    player: Mapping[str, Any], damage_total: int, net_worth_total: int, duration: Any
) -> dict[str, Any]:
    item_uses = player.get("item_uses") or {}
    lh_t = player.get("lh_t") or []
    metrics = dict(player)
    metrics.update(
        id=player.get("account_id"),
        name=player.get("personaname") or str(player.get("account_id") or "Unknown"),
        hero=player.get("hero_name")
        or _hero_name(player.get("hero_id"))
        or str(player.get("hero_id") or "Unknown"),
        role=_lane_role(player),
        gpm=player.get("gold_per_min"),
        xpm=player.get("xp_per_min"),
        lh_at_10=lh_t[9] if len(lh_t) > 9 else None,
        tp_uses=item_uses.get("tpscroll"),
        buyback_count=player.get("buyback_count"),
        damage_share=(player.get("hero_damage") or 0) / damage_total
        if damage_total
        else None,
        gold_share=(player.get("net_worth") or 0) / net_worth_total
        if net_worth_total
        else None,
        kda_ratio=(player.get("kills", 0) + player.get("assists", 0))
        / max(player.get("deaths", 0), 1),
        duration=duration,
    )
    return metrics


async def _store_parsed_match(
    session: AsyncSession,
    case: Match,
    data: Mapping[str, Any],
    player_by_steam_id: dict[int, Player],
) -> None:
    raw_players = data["players"]
    our_radiant = case.our_side == "radiant"
    our_raw = [
        player
        for player in raw_players
        if _is_radiant(int(player.get("player_slot", 0))) == our_radiant
    ]
    damage_total = sum(int(player.get("hero_damage") or 0) for player in our_raw)
    net_worth_total = sum(int(player.get("net_worth") or 0) for player in our_raw)
    team = [
        _metric_player(player, damage_total, net_worth_total, data.get("duration"))
        for player in our_raw
    ]
    result = accuse(
        _load_meme_db(),
        team,
        mode="private",
        contexts=["victory" if case.we_won else "defeat"],
        seed=case.match_id,
    )

    await session.execute(delete(MatchPlayer).where(MatchPlayer.match_id == case.id))
    for raw_player in raw_players:
        is_our_team = _is_radiant(int(raw_player.get("player_slot", 0))) == our_radiant
        damage_base = damage_total if is_our_team else sum(
            int(player.get("hero_damage") or 0)
            for player in raw_players
            if _is_radiant(int(player.get("player_slot", 0))) != our_radiant
        )
        side = [
            player
            for player in raw_players
            if (_is_radiant(int(player.get("player_slot", 0))) == our_radiant)
            == is_our_team
        ]
        metrics = _metric_player(
            raw_player,
            damage_base,
            sum(int(player.get("net_worth") or 0) for player in side),
            data.get("duration"),
        )
        known_player = player_by_steam_id.get(raw_player.get("account_id"))
        session.add(
            MatchPlayer(
                match_id=case.id,
                player_id=known_player.id if known_player else None,
                hero_id=int(raw_player.get("hero_id") or 0),
                hero_name=raw_player.get("hero_name")
                or _hero_name(raw_player.get("hero_id")),
                lane_role=metrics["role"],
                is_our_team=is_our_team,
                kills=raw_player.get("kills"),
                deaths=raw_player.get("deaths"),
                assists=raw_player.get("assists"),
                gpm=metrics["gpm"],
                xpm=metrics["xpm"],
                net_worth=raw_player.get("net_worth"),
                lh_at_10=metrics["lh_at_10"],
                damage_share=metrics["damage_share"],
                teamfight_participation=raw_player.get("teamfight_participation"),
                obs_placed=raw_player.get("obs_placed"),
                sen_placed=raw_player.get("sen_placed"),
                tp_uses=metrics["tp_uses"],
                buybacks=metrics["buyback_count"],
                stuns=raw_player.get("stuns"),
                tower_damage=raw_player.get("tower_damage"),
                metrics_json=json.dumps(metrics, ensure_ascii=False),
            )
        )
    case.started_at = _timestamp(data.get("start_time"))
    case.duration = data.get("duration")
    case.radiant_win = data.get("radiant_win")
    case.we_won = case.radiant_win == our_radiant
    case.raw_json = json.dumps(data, ensure_ascii=False)
    case.evidence_json = json.dumps(
        {str(item["player"]["id"]): item["evidence"] for item in result["suspects"]},
        ensure_ascii=False,
    )
    case.nominees_json = json.dumps(result, ensure_ascii=False)
    case.parse_status = "parsed"
    await session.commit()


def _load_meme_db() -> dict[str, Any]:
    path = Path(__file__).parents[1] / "data" / "memes.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _hero_names_zh() -> dict[str, str]:
    """hero_id → 中文英雄名。

    OpenDota 的 /matches/{id} 只给 hero_id，不给 hero_name，前端直接显示会漏出
    「121」这种数字。映射表取自 Valve 官方 datafeed（schinese），静态落盘，
    不在请求路径上依赖外网。新版本加英雄时重跑 scripts/refresh_heroes.py。
    """
    path = Path(__file__).parents[1] / "data" / "heroes_zh.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _hero_name(hero_id: Any) -> str | None:
    if hero_id is None:
        return None
    return _hero_names_zh().get(str(hero_id))


async def polling_loop(factory: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            async with factory() as session, OpenDotaClient() as client:
                await poll_once(session, client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("OpenDota polling cycle failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
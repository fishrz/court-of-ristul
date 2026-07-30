"""把已登记玩家最近的开黑局回溯进案卷库。

poller 只往前看：它每 5 分钟扫一次 recentMatches，新局才建案。
第一次上线时案卷库是空的，得靠这个脚本把历史局灌进去。

用法（在 backend/ 下）：
    .venv/bin/python -m scripts.backfill --limit 10
    .venv/bin/python -m scripts.backfill --limit 10 --dry-run

OpenDota 的 /matches/{id} 对未 parse 的比赛只返回基础字段，拿不到
lh_t / obs_placed / teamfight_participation 这些归因要用的指标。所以流程是：
建案 → POST /request/{id} 请求解析 → 轮询等 OpenDota 解析完 → 入库。
解析队列排队时间不定，脚本会等，等不到的就留 parse_status='parsing'，
之后 poller 的每轮循环会自己把它捡起来收尾。
"""

import argparse
import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Match, Player
from app.opendota import OpenDotaClient
from app.poller import (
    MIN_PARTY_SIZE,
    MIN_REGISTERED,
    _is_radiant,
    _match_is_parsed,
    _store_parsed_match,
    _timestamp,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill")

PARSE_POLL_INTERVAL = 15
PARSE_MAX_WAIT = 300


async def collect_candidates(
    client: OpenDotaClient, players: list[Player]
) -> dict[tuple[int, bool], dict[int, Mapping[str, Any]]]:
    candidates: dict[tuple[int, bool], dict[int, Mapping[str, Any]]] = defaultdict(dict)
    for player in players:
        recent = await client.get_recent_matches(player.steam_id)
        if not isinstance(recent, list):
            logger.warning("拿不到 %s 的最近比赛，跳过", player.display_name)
            continue
        logger.info("%s: %d 场最近比赛", player.display_name, len(recent))
        for match in recent:
            if not isinstance(match, Mapping) or match.get("match_id") is None:
                continue
            key = (int(match["match_id"]), _is_radiant(int(match.get("player_slot", 0))))
            candidates[key][player.id] = match
    return candidates


def qualifies(members: Mapping[int, Mapping[str, Any]]) -> bool:
    if len(members) >= MIN_REGISTERED:
        return True
    return any(
        (recent.get("party_size") or 0) >= MIN_PARTY_SIZE for recent in members.values()
    )


async def wait_for_parse(client: OpenDotaClient, match_id: int) -> Any | None:
    """请求解析并等待。等不到就返回 None，交给 poller 后续收尾。"""
    await client.request_parse(match_id)
    waited = 0
    while waited < PARSE_MAX_WAIT:
        await asyncio.sleep(PARSE_POLL_INTERVAL)
        waited += PARSE_POLL_INTERVAL
        data = await client.get_match(match_id)
        if _match_is_parsed(data):
            return data
        logger.info("  %s 仍在解析队列中（已等 %ds）", match_id, waited)
    return None


async def main(limit: int, dry_run: bool) -> None:
    async with SessionLocal() as session, OpenDotaClient() as client:
        players = list(
            await session.scalars(select(Player).where(Player.is_active.is_(True)))
        )
        if not players:
            logger.error("库里没有已登记玩家，先去 /join 登记")
            return
        logger.info("已登记玩家 %d 人：%s", len(players), "、".join(p.display_name for p in players))

        candidates = await collect_candidates(client, players)
        eligible = [
            (match_id, radiant, members)
            for (match_id, radiant), members in candidates.items()
            if qualifies(members)
        ]
        # 新的在前
        eligible.sort(
            key=lambda item: next(iter(item[2].values())).get("start_time") or 0,
            reverse=True,
        )
        eligible = eligible[:limit]
        logger.info("符合建案条件的开黑局 %d 场", len(eligible))

        for match_id, radiant_side, members in eligible:
            sample = next(iter(members.values()))
            tag = f"{match_id}（{len(members)} 人登记，party={sample.get('party_size')}）"
            if await session.scalar(select(Match.id).where(Match.match_id == match_id)):
                logger.info("跳过 %s：已在库中", tag)
                continue
            if dry_run:
                logger.info("[dry-run] 会建案 %s", tag)
                continue

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
            await session.commit()
            logger.info("建案 %s，请求解析…", tag)

            data = await wait_for_parse(client, match_id)
            if data is None:
                logger.warning("  %s 解析未完成，留给 poller 收尾", match_id)
                continue
            await _store_parsed_match(
                session, case, data, {p.steam_id: p for p in players}
            )
            logger.info("  %s 入库完成（%s）", match_id, "胜" if case.we_won else "负")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回溯最近的开黑局到案卷库")
    parser.add_argument("--limit", type=int, default=10, help="最多回溯几局（默认 10）")
    parser.add_argument("--dry-run", action="store_true", help="只看会抓哪些局，不写库")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.dry_run))

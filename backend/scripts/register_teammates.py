"""从比赛原始包登记常驻队友并回填历史出场绑定。

默认只预演；传入 ``--apply`` 才写入数据库。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Match, MatchPlayer, Player

OWNER_ACCOUNT_ID = 114468354
MIN_MATCHES = 3


@dataclass
class Teammate:
    matches: set[int] = field(default_factory=set)
    names: Counter[str] = field(default_factory=Counter)

    @property
    def display_name(self) -> str:
        return self.names.most_common(1)[0][0] if self.names else "Unknown"


def _raw_players(raw_json: str | None) -> list[Mapping[str, Any]]:
    if not raw_json:
        return []
    try:
        data = json.loads(raw_json)
    except (TypeError, ValueError):
        return []
    players = data.get("players") if isinstance(data, Mapping) else None
    if not isinstance(players, list):
        return []
    return [player for player in players if isinstance(player, Mapping)]


def _is_radiant(player: Mapping[str, Any]) -> bool:
    return int(player.get("player_slot") or 0) < 128


def _teammates(matches: list[Match]) -> dict[int, Teammate]:
    found: dict[int, Teammate] = {}
    for match in matches:
        players = _raw_players(match.raw_json)
        owner = next(
            (player for player in players if player.get("account_id") == OWNER_ACCOUNT_ID),
            None,
        )
        if owner is None:
            continue
        owner_radiant = _is_radiant(owner)
        seen: set[int] = set()
        for player in players:
            account_id = player.get("account_id")
            if (
                not isinstance(account_id, int)
                or account_id <= 0
                or account_id == OWNER_ACCOUNT_ID
                or account_id in seen
                or _is_radiant(player) != owner_radiant
            ):
                continue
            seen.add(account_id)
            teammate = found.setdefault(account_id, Teammate())
            teammate.matches.add(match.id)
            name = player.get("personaname")
            if isinstance(name, str) and name.strip():
                teammate.names[name.strip()] += 1
    return {
        account_id: teammate
        for account_id, teammate in found.items()
        if len(teammate.matches) >= MIN_MATCHES
    }


def _account_by_slot(match: Match) -> dict[tuple[int, bool], int]:
    """返回 (hero_id, is_our_team) -> account_id，用现有列安全回链。"""
    players = _raw_players(match.raw_json)
    our_radiant = match.our_side == "radiant"
    result: dict[tuple[int, bool], int] = {}
    for player in players:
        hero_id = player.get("hero_id")
        account_id = player.get("account_id")
        if not isinstance(hero_id, int) or not isinstance(account_id, int) or account_id <= 0:
            continue
        is_our_team = _is_radiant(player) == our_radiant
        result[(hero_id, is_our_team)] = account_id
    return result


async def register_teammates(session: AsyncSession, *, apply: bool) -> dict[str, int]:
    matches = list(await session.scalars(select(Match).order_by(Match.id)))
    teammates = _teammates(matches)
    existing = {
        int(player.steam_id): player for player in await session.scalars(select(Player))
    }
    to_create = [account_id for account_id in teammates if account_id not in existing]

    if apply:
        for account_id in to_create:
            teammate = teammates[account_id]
            player = Player(
                steam_id=account_id,
                display_name=teammate.display_name,
            )
            session.add(player)
            existing[account_id] = player
        await session.flush()

    linked = 0
    for match in matches:
        accounts = _account_by_slot(match)
        rows = list(
            await session.scalars(
                select(MatchPlayer).where(
                    MatchPlayer.match_id == match.id,
                    MatchPlayer.player_id.is_(None),
                )
            )
        )
        for row in rows:
            account_id = accounts.get((row.hero_id, row.is_our_team))
            if account_id not in teammates:
                continue
            linked += 1
            if apply:
                row.player_id = existing[account_id].id

    if apply:
        await session.commit()

    mode = "写入" if apply else "预演"
    print(f"[{mode}] 将创建 Player {len(to_create)} 人，回填 match_players {linked} 行")
    for account_id, teammate in sorted(
        teammates.items(), key=lambda item: (-len(item[1].matches), item[0])
    ):
        status = "已登记" if account_id in existing and account_id not in to_create else "待创建"
        print(
            f"  {account_id:<12} {len(teammate.matches):>2} 局  "
            f"{teammate.display_name}  ({status})"
        )
    if not apply:
        print("这是预演；加 --apply 才会写入数据库。")
    return {"created": len(to_create), "linked": linked}


async def main() -> int:
    parser = argparse.ArgumentParser(description="登记常驻队友并回填历史比赛绑定")
    parser.add_argument("--apply", action="store_true", help="真正写库；默认只预演")
    args = parser.parse_args()
    async with SessionLocal() as session:
        await register_teammates(session, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""用修正后的位置逻辑重算历史比赛的 lane_role。

为什么需要：旧实现把 OpenDota 的 lane_role（分路）当成 position（位置）
直接映射，导致每个阵营出现两个「一号位」。修复只影响新解析的比赛，
库里已存的记录仍是错的，必须重算。

不需要重新请求 OpenDota——raw_json 里已有 lane / net_worth。

    python -m scripts.rebuild_roles            # 只看会改什么
    python -m scripts.rebuild_roles --apply    # 真正写库
"""

import asyncio
import json
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Match, MatchPlayer
from app.poller import _assign_roles


async def rebuild(apply: bool) -> None:
    changed = 0
    scanned = 0
    skipped = 0
    async with SessionLocal() as session:
        matches = (await session.scalars(select(Match))).all()
        for match in matches:
            if not match.raw_json:
                skipped += 1
                continue
            try:
                data = json.loads(match.raw_json)
            except json.JSONDecodeError:
                skipped += 1
                continue
            players = data.get("players") or []
            if not players:
                skipped += 1
                continue

            roles = _assign_roles(players)
            # raw_json 用 player_slot 定位，DB 行用 hero_id 关联，
            # 因为 MatchPlayer 没有存 player_slot。
            by_hero = {
                int(p.get("hero_id") or 0): roles.get(int(p.get("player_slot") or 0))
                for p in players
            }

            rows = (
                await session.scalars(
                    select(MatchPlayer).where(MatchPlayer.match_id == match.id)
                )
            ).all()
            for row in rows:
                scanned += 1
                new_role = by_hero.get(row.hero_id)
                if new_role is None or new_role == row.lane_role:
                    continue
                print(
                    f"  {match.match_id} hero={row.hero_id:<4} "
                    f"{row.lane_role} -> {new_role}"
                )
                changed += 1
                if apply:
                    row.lane_role = new_role

        if apply:
            await session.commit()

    print(
        f"\n扫描 {scanned} 条选手记录，{changed} 条位置需要修正，"
        f"{skipped} 场比赛缺 raw_json 跳过。"
    )
    if not apply:
        print("这是预演。确认无误后加 --apply 真正写库（建议先备份 court.db）。")


if __name__ == "__main__":
    asyncio.run(rebuild("--apply" in sys.argv))

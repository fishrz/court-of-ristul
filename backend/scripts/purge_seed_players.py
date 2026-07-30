"""删除 seed_dev 灌进来的占位玩家，让队友走 /join 自助登记。

占位玩家的 steam_id 是 7000000xx（我编的号段，不是真实 Steam 账号）。
真人的 steam_id 来自 OpenDota，不可能落在这个段里，所以按号段识别是安全的。

必须按外键顺序删：votes → attendances → match_players.player_id 解绑 → players。
match_players 行本身不能删——那是 OpenDota 抓来的真实比赛数据，
只是把指向假人的 player_id 置空，等真人登记后由回填脚本重新绑定。

    .venv/bin/python -m scripts.purge_seed_players          # 预览
    .venv/bin/python -m scripts.purge_seed_players --apply  # 执行
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.models import Attendance, MatchPlayer, Player, Trial, Vote

SEED_ID_MIN = 700000000
SEED_ID_MAX = 799999999


async def main(apply: bool) -> int:
    async with SessionLocal() as session:
        seeds = list(
            await session.scalars(
                select(Player).where(
                    Player.steam_id >= SEED_ID_MIN,
                    Player.steam_id <= SEED_ID_MAX,
                )
            )
        )
        if not seeds:
            print("没有占位玩家，无需清理。")
            return 0

        ids = [p.id for p in seeds]
        print("将删除以下占位玩家：")
        for p in seeds:
            print(f"  id={p.id:<3} steam_id={p.steam_id:<12} {p.display_name}")

        votes = len(list(await session.scalars(
            select(Vote).where(Vote.voter_id.in_(ids) | Vote.nominee_id.in_(ids))
        )))
        atts = len(list(await session.scalars(
            select(Attendance).where(Attendance.player_id.in_(ids))
        )))
        mps = len(list(await session.scalars(
            select(MatchPlayer).where(MatchPlayer.player_id.in_(ids))
        )))
        trials = len(list(await session.scalars(
            select(Trial).where(
                Trial.verdict_player_id.in_(ids)
                | Trial.ai_verdict_player_id.in_(ids)
            )
        )))
        print(f"\n连带影响：投票 {votes} · 到庭 {atts} · 判决引用 {trials}")
        print(f"         {mps} 条真实比赛出场记录将解绑（保留数据，仅置空 player_id）")

        if not apply:
            print("\n预览模式。确认无误后加 --apply 执行。")
            return 0

        await session.execute(
            delete(Vote).where(Vote.voter_id.in_(ids) | Vote.nominee_id.in_(ids))
        )
        await session.execute(delete(Attendance).where(Attendance.player_id.in_(ids)))
        # 判决指向假人就失去意义了，连同该 trial 一起清掉，
        # 免得宣判页渲染出一个已不存在的被告。
        stale = list(await session.scalars(
            select(Trial).where(
                Trial.verdict_player_id.in_(ids)
                | Trial.ai_verdict_player_id.in_(ids)
            )
        ))
        for trial in stale:
            await session.execute(delete(Vote).where(Vote.trial_id == trial.id))
            await session.execute(
                delete(Attendance).where(Attendance.trial_id == trial.id)
            )
            await session.delete(trial)
        await session.execute(
            update(MatchPlayer)
            .where(MatchPlayer.player_id.in_(ids))
            .values(player_id=None)
        )
        for player in seeds:
            await session.delete(player)
        await session.commit()

        left = list(await session.scalars(select(Player)))
        print(f"\n已清理。剩余玩家 {len(left)} 人：")
        for p in left:
            print(f"  id={p.id:<3} steam_id={p.steam_id:<12} {p.display_name}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--apply" in sys.argv)))

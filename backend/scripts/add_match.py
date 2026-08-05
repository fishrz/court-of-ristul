"""按 match_id 手动补录比赛 —— 捞回 poller 漏掉的局。

为什么需要：
    poller 只看 OpenDota 的 recent 窗口（最近 20 局）。早期的 party_size
    三态 bug 会把「还没解析」的五黑局当成「没开黑」永久丢弃，等 bug 修好，
    这些局已经滑出 recent 窗口，poller 再也看不到它们了。
    修 bug 只能防住以后，捞回过去得靠这个脚本。

用法：
    python -m scripts.add_match 8928973355 8929041534            # 预演
    python -m scripts.add_match 8928973355 8929041534 --apply    # 真正写库

安全性：
    库里已存在的 match_id 会跳过，不会覆盖。
    比赛里必须至少有一个已登记玩家，否则无法判断哪边是「我们」，直接拒绝。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Match, Player
from app.opendota import OpenDotaClient
from app.poller import _is_radiant, _match_is_parsed, _store_parsed_match, _timestamp


async def add_one(session, client, match_id: int, apply: bool) -> bool:
    if await session.scalar(select(Match.id).where(Match.match_id == match_id)):
        print(f"{match_id}: 库里已经有了，跳过")
        return False

    data = await client.get_match(match_id)
    if not isinstance(data, dict) or not isinstance(data.get("players"), list):
        print(f"{match_id}: OpenDota 拉不到这局")
        return False

    players = await session.scalars(select(Player).where(Player.is_active.is_(True)))
    by_steam_id = {p.steam_id: p for p in players}

    # 我方阵营由「已登记玩家站在哪边」决定，没有登记玩家就无从判断。
    ours = [
        p for p in data["players"] if by_steam_id.get(p.get("account_id")) is not None
    ]
    if not ours:
        print(f"{match_id}: 这局里没有已登记的玩家，无法判断我方阵营")
        return False
    our_radiant = _is_radiant(int(ours[0].get("player_slot") or 0))

    radiant_win = data.get("radiant_win")
    who = "、".join(
        by_steam_id[p["account_id"]].display_name for p in ours if p.get("account_id")
    )
    side = "天辉" if our_radiant else "夜魇"
    won = "胜" if radiant_win == our_radiant else "负"
    parsed = _match_is_parsed(data)
    print(
        f"{match_id}: {side} {won} | 登记玩家 {who} | "
        f"{'已解析' if parsed else '未解析（详细诊断会缺）'}"
    )
    if not apply:
        return True

    case = Match(
        match_id=match_id,
        started_at=_timestamp(data.get("start_time")),
        duration=data.get("duration"),
        radiant_win=radiant_win,
        our_side="radiant" if our_radiant else "dire",
        we_won=(radiant_win == our_radiant) if radiant_win is not None else None,
        parse_status="parsing",
    )
    session.add(case)
    await session.commit()

    if parsed:
        # 复用 poller 的解析入口，位置识别、指标、归因全都跟正常流程一致
        await _store_parsed_match(session, case, data, by_steam_id)
        print(f"{match_id}: 已入库并完成解析")
    else:
        await client.request_parse(match_id)
        print(f"{match_id}: 已入库，已请求 OpenDota 解析，等 poller 下一轮补齐")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description="按 match_id 手动补录比赛")
    parser.add_argument("match_ids", nargs="+", type=int)
    parser.add_argument("--apply", action="store_true", help="真正写库")
    args = parser.parse_args()

    async with SessionLocal() as session, OpenDotaClient() as client:
        added = 0
        for match_id in args.match_ids:
            if await add_one(session, client, match_id, args.apply):
                added += 1

    print(f"\n{added} 局{'已补录' if args.apply else '可补录'}。")
    if not args.apply and added:
        print("确认无误后加 --apply 真正写库。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

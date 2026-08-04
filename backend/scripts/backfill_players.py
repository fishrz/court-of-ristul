"""历史比赛回填 —— 把队友登记之前的比赛认领回来。

要解决的问题：
    poller 抓比赛时，队友还没在 /join 登记，所以 match_players.player_id
    是 NULL。等他们登记了，历史比赛却不会自动补上——个人卷宗从 0 场
    开始攒，得打两周才有话说。这对一个「赛后复盘」产品是致命的冷启动。

为什么能回填：
    OpenDota 的 raw_json 里存着每个玩家的 account_id（除非对方隐藏战绩）。
    account_id 就是 steam_id 的 32 位形式，和 players.steam_id 直接对得上。
    也就是说数据一直都在，只是没建立关联。

用法：
    python -m scripts.backfill_players            # 预演，只报告不写库
    python -m scripts.backfill_players --apply    # 真正写入

安全性：
    只填 player_id 为 NULL 的行，绝不覆盖已有绑定——已绑定的可能是
    人工修正过的，脚本没资格推翻。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Match, MatchPlayer, Player


def _account_ids(raw: str | None) -> dict[int, int]:
    """从原始包里取 {hero_id: account_id}。

    用 hero_id 当键是因为 match_players 表里没存 account_id，
    而同一局同一边不会出现两个相同英雄，所以 hero_id 是可靠的连接键。
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    players = data.get("players")
    if not isinstance(players, list):
        return {}

    out: dict[int, int] = {}
    for item in players:
        if not isinstance(item, dict):
            continue
        hero_id = item.get("hero_id")
        account_id = item.get("account_id")
        # account_id 为 None 表示对方隐藏了战绩，这种回填不了，跳过
        if isinstance(hero_id, int) and isinstance(account_id, int) and account_id:
            out[hero_id] = account_id
    return out


async def backfill(session: AsyncSession, *, apply: bool) -> dict[str, int]:
    players = (await session.scalars(select(Player))).all()
    # steam_id 存的就是 32 位 account_id，直接建索引
    by_account = {int(p.steam_id): p for p in players}
    if not by_account:
        print("库里一个登记玩家都没有，无事可做。")
        return {"linked": 0, "scanned": 0}

    print(f"已登记玩家 {len(by_account)} 人：")
    for account_id, player in by_account.items():
        print(f"  id={player.id:<4} account={account_id:<12} {player.display_name}")
    print()

    matches = (await session.scalars(select(Match))).all()
    linked = 0
    scanned = 0
    per_player: dict[str, int] = {}

    for match in matches:
        hero_to_account = _account_ids(match.raw_json)
        if not hero_to_account:
            continue

        rows = (
            await session.scalars(
                select(MatchPlayer).where(
                    MatchPlayer.match_id == match.id,
                    MatchPlayer.player_id.is_(None),
                )
            )
        ).all()

        for row in rows:
            scanned += 1
            account_id = hero_to_account.get(row.hero_id)
            if not account_id:
                continue
            player = by_account.get(account_id)
            if not player:
                continue

            print(
                f"  比赛 {match.match_id}  {row.hero_name or row.hero_id:<10}"
                f" -> {player.display_name} (player_id={player.id})"
            )
            if apply:
                row.player_id = player.id
            linked += 1
            per_player[player.display_name] = per_player.get(player.display_name, 0) + 1

    if apply:
        await session.commit()

    print()
    print(f"扫描未绑定行 {scanned} 条，可认领 {linked} 条")
    for name, count in sorted(per_player.items(), key=lambda x: -x[1]):
        print(f"  {name}: +{count} 场")
    if not apply:
        print("\n这是预演。确认无误后加 --apply 真正写入。")
    else:
        print("\n已写入数据库。")
    return {"linked": linked, "scanned": scanned}


async def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史比赛的玩家绑定")
    parser.add_argument(
        "--apply", action="store_true", help="真正写库；不加则只预演"
    )
    args = parser.parse_args()

    async with SessionLocal() as session:
        await backfill(session, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""开发用种子脚本：把真实比赛数据灌进本地 DB，让前端有东西可接。

用法（在 backend/ 下）：
    .venv/bin/python -m scripts.seed_dev

不参与生产。只为前后端联调提供一份真实、可复现的数据。

注意：tests/fixtures/match_8917764448.json 是**引擎级归一化数据**
（5 名我方队员、指标已算好），不是 OpenDota 原始响应包。
所以这里直接调 engine.accuse，不走 poller._store_parsed_match
（后者要的是带 player_slot/account_id 的原始格式）。
"""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.db import Base, SessionLocal, engine
from app.engine import accuse
from app.models import Match, MatchPlayer, Player
from app.poller import _load_meme_db

BACKEND = Path(__file__).resolve().parent.parent
FIXTURE = BACKEND / "tests/fixtures/match_8917764448.json"


async def main() -> None:
    # 这个脚本会灌入 700000001+ 号段的占位玩家和 fixture 比赛。
    # 生产库一旦被它污染，队友走 /join 登记时会和假人撞名，
    # 而假人没有真实 steam_id，轮询器抓不到他们的比赛。
    # 要在生产跑必须显式声明意图。
    if os.environ.get("COR_ALLOW_SEED") != "1":
        print(
            "拒绝执行：seed_dev 会写入占位玩家，仅供本地开发。\n"
            "确实需要请设置 COR_ALLOW_SEED=1。\n"
            "生产环境请让队友访问 /join 自助登记真实 Steam 账号。"
        )
        return
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    team = data["players"]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        # 1. 五黑成员：fixture 里没有 account_id，用稳定的开发用 steam_id
        players: dict[str, Player] = {}
        for index, raw in enumerate(team):
            steam_id = 700000001 + index
            name = raw["name"]
            existing = await session.scalar(
                select(Player).where(Player.steam_id == steam_id)
            )
            if existing is None:
                existing = Player(
                    steam_id=steam_id, display_name=name, is_active=True
                )
                session.add(existing)
                await session.flush()
            else:
                existing.display_name = name
            players[name] = existing

        # 2. 比赛本体
        match_id = int(data["match_id"])
        case = await session.scalar(select(Match).where(Match.match_id == match_id))
        if case is None:
            case = Match(match_id=match_id)
            session.add(case)
        case.started_at = datetime.now(tz=UTC) - timedelta(hours=3)
        case.duration = data["duration"]
        case.radiant_win = data["radiant_win"]
        case.our_side = "dire" if data["radiant_win"] else "radiant"
        case.we_won = False
        case.parse_status = "parsed"
        case.raw_json = json.dumps(data, ensure_ascii=False)
        await session.flush()

        # 3. 归因：engine 直接吃 fixture 的 players
        result = accuse(
            _load_meme_db(),
            team,
            mode="private",
            contexts=["defeat"],
            seed=match_id,
        )

        # 4. 落 MatchPlayer
        await session.execute(
            delete(MatchPlayer).where(MatchPlayer.match_id == case.id)
        )
        for raw in team:
            session.add(
                MatchPlayer(
                    match_id=case.id,
                    player_id=players[raw["name"]].id,
                    hero_id=0,
                    hero_name=raw.get("hero"),
                    lane_role=raw.get("role"),
                    is_our_team=True,
                    kills=raw.get("kills"),
                    deaths=raw.get("deaths"),
                    assists=raw.get("assists"),
                    gpm=raw.get("gpm"),
                    xpm=raw.get("xpm"),
                    net_worth=raw.get("net_worth"),
                    lh_at_10=raw.get("lh_at_10"),
                    damage_share=raw.get("damage_share"),
                    teamfight_participation=raw.get("teamfight_participation"),
                    obs_placed=raw.get("obs_placed"),
                    sen_placed=raw.get("sen_placed"),
                    tp_uses=raw.get("tp_uses"),
                    buybacks=raw.get("buyback_count"),
                    stuns=raw.get("stuns"),
                    tower_damage=raw.get("tower_damage"),
                    metrics_json=json.dumps(raw, ensure_ascii=False),
                )
            )

        # 5. 归因结果按 poller 的口径写回（key 用 player name，fixture 无 id）
        case.evidence_json = json.dumps(
            {
                str(item["player"].get("id", item["player"]["name"])): item["evidence"]
                for item in result["suspects"]
            },
            ensure_ascii=False,
        )
        case.nominees_json = json.dumps(result, ensure_ascii=False)
        await session.commit()

        print(f"match {match_id}  we_won={case.we_won}  side={case.our_side}")
        print(f"players seeded: {len(players)}")
        print("suspects (guilt desc):")
        for item in result["suspects"]:
            name = item["player"]["name"]
            tags = [e.get("tag") for e in item["evidence"]]
            print(f"  {item['score']:>4}  {name:<12} {tags}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

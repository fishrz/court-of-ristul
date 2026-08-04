"""版本基准层（L2）—— 给个人表现提供「同分段同英雄」的参照系。

为什么需要这一层：
    队内五人对比只能回答「谁最差」，回答不了「差得离不离谱」。
    五个人都打得烂时，倒数第一其实不冤；赢的局里有人在混，队内
    对比也看不出来——大家都在赢，谁都不难看。
    接上全服基准之后才说得出「你这局 GPM 打到同英雄前 3%，但推塔
    伤害只有 60 分位」这种队内对比永远说不出的话。

刻意不做的事：
    不做「本版本胜率榜」。那是 Dotabuff 十秒钟能解决的事，重复别人
    做得更好的东西没有价值，而且和「以数据为证据的赛后审判」这个
    产品内核是两张皮。meta 在这里只当参照系，不当排行榜。

分段（rank_tier 十位）：
    1 先驱 2 守卫 3 中军 4 统帅 5 传奇 6 万古 7 超凡 8 不朽
    heroStats 的字段就叫 1_pick/1_win … 8_pick/8_win。
    同一个英雄在先驱局和超凡局胜率能差 8 个点，拿全服平均给超凡
    玩家提建议等于拿小学生平均分要求高中生。取错档比不取更糟。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MetaSnapshot
from app.opendota import OpenDotaClient

logger = logging.getLogger(__name__)

# 缓存多久算新鲜。OpenDota 这几个端点本身就是天级聚合
# （pub_pick_trend 是按天分桶的 7 元素数组），一天一刷足够。
TTL = timedelta(hours=24)

KIND_HERO_STATS = "hero_stats"
KIND_BENCHMARKS = "benchmarks"
KIND_ITEM_TIMINGS = "item_timings"
KIND_MATCHUPS = "matchups"


def bracket_of(rank_tier: int | None) -> int | None:
    """rank_tier -> heroStats 的分段档位。73（超凡3）-> 7。

    不朽（8）在 heroStats 里 8_pick 恒为 0——样本太少 OpenDota 没有分桶。
    所以不朽一律降到 7 档，这是数据事实，不是我们的取舍。
    """
    if not rank_tier:
        return None
    bracket = int(rank_tier) // 10
    if bracket >= 8:
        return 7
    if bracket < 1:
        return None
    return bracket


async def _load(
    session: AsyncSession, kind: str, hero_id: int = 0
) -> dict[str, Any] | list[Any] | None:
    row = await session.scalar(
        select(MetaSnapshot).where(
            MetaSnapshot.kind == kind, MetaSnapshot.hero_id == hero_id
        )
    )
    if row is None:
        return None
    try:
        return json.loads(row.payload_json)
    except (TypeError, ValueError):
        logger.warning("meta 缓存 %s/%s 解析失败", kind, hero_id)
        return None


def _is_fresh(row: MetaSnapshot | None, patch: str | None) -> bool:
    if row is None:
        return False
    # 版本换了就一律作废——上个版本的出装时间和胜率是过期证据
    if patch and row.patch and row.patch != patch:
        return False
    fetched = row.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return datetime.now(UTC) - fetched < TTL


async def _store(
    session: AsyncSession,
    kind: str,
    hero_id: int,
    payload: Any,
    patch: str | None,
) -> None:
    row = await session.scalar(
        select(MetaSnapshot).where(
            MetaSnapshot.kind == kind, MetaSnapshot.hero_id == hero_id
        )
    )
    if row is None:
        row = MetaSnapshot(kind=kind, hero_id=hero_id)
        session.add(row)
    row.payload_json = json.dumps(payload, ensure_ascii=False)
    row.patch = patch
    row.fetched_at = datetime.now(UTC)


async def current_patch(client: OpenDotaClient) -> str | None:
    data = await client.get_patches()
    if isinstance(data, list) and data:
        name = data[-1].get("name")
        return str(name) if name else None
    return None


async def refresh(
    session: AsyncSession,
    client: OpenDotaClient,
    hero_ids: list[int],
    *,
    force: bool = False,
) -> dict[str, int]:
    """抓取并缓存 meta。只抓我们真正用得到的英雄，不做全量。

    全量 126 个英雄 × 3 个端点 = 378 次请求，限速 1s/次要跑 6 分钟，
    而我们实际只关心队友玩过的那十几个。按需抓是唯一合理的做法。
    """
    patch = await current_patch(client)
    stats: dict[str, int] = {"hero_stats": 0, "benchmarks": 0, "timings": 0, "matchups": 0}

    row = await session.scalar(
        select(MetaSnapshot).where(
            MetaSnapshot.kind == KIND_HERO_STATS, MetaSnapshot.hero_id == 0
        )
    )
    if force or not _is_fresh(row, patch):
        data = await client.get_hero_stats()
        if data:
            await _store(session, KIND_HERO_STATS, 0, data, patch)
            stats["hero_stats"] = 1

    jobs = (
        (KIND_BENCHMARKS, client.get_hero_benchmarks, "benchmarks"),
        (KIND_ITEM_TIMINGS, client.get_item_timings, "timings"),
        (KIND_MATCHUPS, client.get_hero_matchups, "matchups"),
    )
    for hero_id in hero_ids:
        if not hero_id:
            continue
        for kind, fetch, counter in jobs:
            existing = await session.scalar(
                select(MetaSnapshot).where(
                    MetaSnapshot.kind == kind, MetaSnapshot.hero_id == hero_id
                )
            )
            if not force and _is_fresh(existing, patch):
                continue
            data = await fetch(hero_id)
            if data:
                await _store(session, kind, hero_id, data, patch)
                stats[counter] += 1

    await session.commit()
    return stats


# ---- 读取侧：把缓存翻译成能直接下判断的数字 --------------------------


async def hero_winrate(
    session: AsyncSession, hero_id: int, bracket: int | None
) -> dict[str, Any] | None:
    """该英雄在指定分段的基准胜率。样本不足直接返回 None，不返回噪音。"""
    data = await _load(session, KIND_HERO_STATS, 0)
    if not isinstance(data, list) or bracket is None:
        return None
    hero = next((h for h in data if h.get("id") == hero_id), None)
    if not hero:
        return None
    picks = hero.get(f"{bracket}_pick") or 0
    wins = hero.get(f"{bracket}_win") or 0
    # 500 场以下的分段样本波动太大，胜率小数点后一位没有意义
    if picks < 500:
        return None
    return {
        "hero_id": hero_id,
        "name": hero.get("localized_name"),
        "bracket": bracket,
        "picks": picks,
        "winrate": round(wins / picks * 100, 1),
    }


async def item_timing_median(
    session: AsyncSession, hero_id: int, item: str
) -> dict[str, Any] | None:
    """该英雄某件关键装备在「赢下的局」里的典型出装时间。

    刻意只统计胜局：我们要回答的是「赢的人什么时候出的」，
    而不是「所有人平均什么时候出」——后者被输的局拉长，
    拿它当目标等于把失败样本当标杆。
    """
    data = await _load(session, KIND_ITEM_TIMINGS, hero_id)
    if not isinstance(data, list):
        return None
    rows = [r for r in data if r.get("item") == item]
    if not rows:
        return None

    total_wins = 0
    buckets: list[tuple[int, int]] = []
    for r in rows:
        try:
            wins = int(r.get("wins") or 0)
            time_s = int(r.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if wins <= 0:
            continue
        buckets.append((time_s, wins))
        total_wins += wins
    if total_wins < 50:
        return None

    buckets.sort()
    half = total_wins / 2
    seen = 0
    median = buckets[-1][0]
    for time_s, wins in buckets:
        seen += wins
        if seen >= half:
            median = time_s
            break
    return {"item": item, "median_seconds": median, "sample": total_wins}


async def worst_matchups(
    session: AsyncSession, hero_id: int, enemy_hero_ids: list[int], limit: int = 2
) -> list[dict[str, Any]]:
    """本局实际对上的敌方英雄里，哪些是这个英雄的天敌。

    这条的产品价值是能替玩家洗清一部分冤屈——「你不是打得烂，
    是这局对位天然吃亏」。法庭偶尔判无罪，比场场定罪更可信。
    """
    data = await _load(session, KIND_MATCHUPS, hero_id)
    if not isinstance(data, list) or not enemy_hero_ids:
        return []
    wanted = set(enemy_hero_ids)
    out: list[dict[str, Any]] = []
    for row in data:
        if row.get("hero_id") not in wanted:
            continue
        games = row.get("games_played") or 0
        wins = row.get("wins") or 0
        if games < 200:
            continue
        rate = wins / games * 100
        if rate >= 47.0:
            continue
        out.append(
            {
                "enemy_hero_id": row["hero_id"],
                "winrate": round(rate, 1),
                "games": games,
            }
        )
    out.sort(key=lambda x: x["winrate"])
    return out[:limit]

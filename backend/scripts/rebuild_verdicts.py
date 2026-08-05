"""用当前的位置逻辑与词库重算历史比赛的罪证 / 提名快照。

为什么需要：evidence_json 与 nominees_json 是建案当时算好写死的快照，
里面嵌了那一刻的 role。rebuild_roles 只修 match_players.lane_role，
不碰这两个快照，于是「速报名单」显示四号位、而「罪证 / 提名」页仍按
旧 role 触发劣单词条——同一局同一个人，两个页面两个位置。

不请求 OpenDota：raw_json 里已有全部字段，重算是纯本地推导。

    python -m scripts.rebuild_verdicts            # 只看会改什么
    python -m scripts.rebuild_verdicts --apply    # 真正写库
    python -m scripts.rebuild_verdicts 8929041534 --apply   # 只重算指定比赛
"""

import asyncio
import json
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.engine import accuse
from app.models import Match
from app.poller import _assign_roles, _is_radiant, _load_meme_db, _metric_player


def _recompute(match: Match, data: dict) -> tuple[dict, dict] | None:
    """返回 (evidence_map, nominees_result)，数据不足时返回 None。"""
    raw_players = data.get("players") or []
    if not raw_players:
        return None
    our_radiant = match.our_side == "radiant"
    our_raw = [
        p
        for p in raw_players
        if _is_radiant(int(p.get("player_slot", 0))) == our_radiant
    ]
    if not our_raw:
        return None

    damage_total = sum(int(p.get("hero_damage") or 0) for p in our_raw)
    net_worth_total = sum(int(p.get("net_worth") or 0) for p in our_raw)
    roles = _assign_roles(raw_players)
    team = [
        _metric_player(p, damage_total, net_worth_total, data.get("duration"), roles)
        for p in our_raw
    ]
    result = accuse(
        _load_meme_db(),
        team,
        mode="private",
        contexts=["victory" if match.we_won else "defeat"],
        seed=match.match_id,
    )
    evidence = {
        str(item["player"]["id"]): item["evidence"] for item in result["suspects"]
    }
    return evidence, result


def _roles_of(payload: str | None) -> dict[str, str]:
    """从 nominees_json 快照里提取 hero -> role，用于打印差异。"""
    if not payload:
        return {}
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    out = {}
    for item in data.get("suspects") or []:
        player = item.get("player") or {}
        hero = player.get("hero")
        if hero:
            out[hero] = player.get("role")
    return out


async def rebuild(apply: bool, only: set[int]) -> None:
    changed = 0
    skipped = 0
    async with SessionLocal() as session:
        matches = (await session.scalars(select(Match))).all()
        for match in matches:
            if only and match.match_id not in only:
                continue
            if not match.raw_json:
                skipped += 1
                continue
            try:
                data = json.loads(match.raw_json)
            except json.JSONDecodeError:
                skipped += 1
                continue

            recomputed = _recompute(match, data)
            if recomputed is None:
                skipped += 1
                continue
            evidence, result = recomputed

            old_roles = _roles_of(match.nominees_json)
            new_roles = _roles_of(json.dumps(result, ensure_ascii=False))
            diffs = [
                f"{hero} {old_roles.get(hero)} -> {role}"
                for hero, role in new_roles.items()
                if old_roles.get(hero) != role
            ]

            evidence_json = json.dumps(evidence, ensure_ascii=False)
            nominees_json = json.dumps(result, ensure_ascii=False)
            if (
                evidence_json == match.evidence_json
                and nominees_json == match.nominees_json
            ):
                continue

            changed += 1
            print(f"  {match.match_id}: " + ("; ".join(diffs) if diffs else "快照内容变化"))
            if apply:
                match.evidence_json = evidence_json
                match.nominees_json = nominees_json

        if apply:
            await session.commit()

    print(f"\n{changed} 场比赛的罪证/提名快照需要重算，{skipped} 场缺 raw_json 跳过。")
    if not apply:
        print("这是预演。确认无误后加 --apply 真正写库（建议先备份 court.db）。")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    asyncio.run(rebuild("--apply" in sys.argv, {int(a) for a in args}))

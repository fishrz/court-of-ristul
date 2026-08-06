"""用当前副奖逻辑回填历史 closed trial 的 verdict_json.side_award。

    python -m scripts.rebuild_side_awards            # 只看会改什么
    python -m scripts.rebuild_side_awards --apply    # 真正写库
"""

import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.award import pick_side_award
from app.db import SessionLocal
from app.models import Match, MatchPlayer, Trial


def recompute_verdict(trial: Trial) -> dict[str, Any] | None:
    if not trial.verdict_json:
        return None
    try:
        payload = json.loads(trial.verdict_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    payload["side_award"] = pick_side_award(trial.match, trial.verdict_player_id)
    return payload


async def rebuild(apply: bool) -> None:
    changed = 0
    skipped = 0
    async with SessionLocal() as session:
        trials = (
            await session.scalars(
                select(Trial)
                .where(Trial.status == "closed")
                .options(
                    selectinload(Trial.match)
                    .selectinload(Match.players)
                    .selectinload(MatchPlayer.player)
                )
                .order_by(Trial.id)
            )
        ).all()
        for trial in trials:
            payload = recompute_verdict(trial)
            if payload is None:
                skipped += 1
                continue

            old_payload = json.loads(trial.verdict_json)
            if old_payload.get("side_award") == payload["side_award"] and "side_award" in old_payload:
                continue

            changed += 1
            award = payload["side_award"]
            detail = "null（无正分亮点）"
            if award is not None:
                detail = f"{award['name']} / {award['fact']} / {award['quip']}"
            print(f"  trial {trial.id} / match {trial.match.match_id}: {detail}")
            if apply:
                trial.verdict_json = json.dumps(payload, ensure_ascii=False)

        if apply:
            await session.commit()

    print(f"\n{changed} 场历史判决需要回填副奖，{skipped} 场判决 JSON 无效跳过。")
    if not apply:
        print("这是预演。确认无误后加 --apply 真正写库（请先备份 court.db）。")


if __name__ == "__main__":
    asyncio.run(rebuild("--apply" in sys.argv[1:]))

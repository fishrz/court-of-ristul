import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.engine import _format_value, select
from app.models import Match, MatchPlayer
from app.verdict_copy import copy_for

_HIGHLIGHTS = (
    ("damage_share", "damage_share"),
    ("tower_damage", "tower_damage"),
    ("teamfight_participation", "teamfight_participation"),
    ("vision", "vision"),
    ("kda_ratio", "kda_ratio"),
)


@lru_cache(maxsize=1)
def _meme_db() -> dict[str, Any]:
    path = Path(__file__).parents[1] / "data" / "memes.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(row: MatchPlayer) -> dict[str, Any]:
    stored = json.loads(row.metrics_json) if row.metrics_json else {}
    stored.update(
        id=row.player_id,
        name=row.player.display_name if row.player else "玩家",
        hero=row.hero_name or str(row.hero_id),
        damage_share=row.damage_share,
        tower_damage=row.tower_damage,
        teamfight_participation=row.teamfight_participation,
        obs_placed=row.obs_placed,
        sen_placed=row.sen_placed,
        vision=(row.obs_placed or 0) + (row.sen_placed or 0),
    )
    return stored


def pick_side_award(
    case: Match, exclude_player_id: int | None
) -> dict[str, Any] | None:
    rows = [
        row
        for row in case.players
        if row.is_our_team and row.player_id is not None
    ]
    candidates = [row for row in rows if row.player_id != exclude_player_id]
    if not candidates:
        return None

    metrics_by_id = {row.player_id: _metrics(row) for row in rows}
    maxima = {
        metric: max(
            (
                values.get(source)
                for values in metrics_by_id.values()
                if values.get(source) is not None
            ),
            default=None,
        )
        for metric, source in _HIGHLIGHTS
    }

    scored: list[tuple[MatchPlayer, dict[str, Any], list[str]]] = []
    for row in candidates:
        values = metrics_by_id[row.player_id]
        hits = []
        for metric, source in _HIGHLIGHTS:
            value = values.get(source)
            maximum = maxima[metric]
            if (
                value is not None
                and value > 0
                and value == maximum
            ):
                hits.append(metric)
        scored.append((row, values, hits))

    winner, values, hits = min(
        scored,
        key=lambda item: (
            -len(item[2]),
            -(item[1].get("damage_share") or 0),
            item[1].get("gold_share") or 0,
            item[0].player_id,
        ),
    )
    if not hits:
        return None

    db = _meme_db()
    metric_defs = db["metrics"]
    basis = []
    for metric in hits:
        source = dict(_HIGHLIGHTS)[metric]
        value = values[source]
        if metric == "vision":
            label = "视野投入"
            formatted = str(value)
        else:
            label = metric_defs[metric]["label"]
            formatted = _format_value(metric, value, metric_defs)
        basis.append({"metric": metric, "label": label, "value": formatted})

    tone = "praise" if case.we_won else "comfort"
    evidence = select(
        db,
        values,
        list(metrics_by_id.values()),
        mode="private",
        contexts=["victory" if case.we_won else "defeat"],
        tones={tone},
        max=1,
        seed=case.match_id * 100 + winner.player_id,
        includeFallback=True,
    )
    return {
        "player_id": winner.player_id,
        "name": values["name"],
        "hero": values["hero"],
        "tag": copy_for(case.we_won)["side_award_tag"],
        "fact": "、".join(
            f"{item['label']} {item['value']}" for item in basis[:2]
        ),
        "quip": evidence[0]["quip"] if evidence else None,
        "basis": basis,
    }

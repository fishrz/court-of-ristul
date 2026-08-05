"""Data-only attribution engine, behaviorally equivalent to ``memes-engine.js``."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

MODE_LIMITS = {"private": 3, "public": 1, "safe": 0}
NON_ACCUSATORY = {"court", "loading", "share"}
PARSE_ONLY = {
    "lh_at_10",
    "lane_role",
    "teamfight_participation",
    "stuns",
    "obs_placed",
    "sen_placed",
}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def rank_of(players: Sequence[Mapping[str, Any]], metric: str, player_id: Any) -> int | None:
    """Return a zero-based descending team rank, excluding missing values."""
    ranked = sorted(
        (player for player in players if player.get(metric) is not None),
        key=lambda player: player[metric],
        reverse=True,
    )
    return next(
        (index for index, player in enumerate(ranked) if player.get("id") == player_id),
        None,
    )


def _eval_condition(
    condition: Mapping[str, Any], context: Mapping[str, Any]
) -> bool:
    metric = condition["metric"]
    operator = condition["op"]
    expected = condition["value"]
    player = context["player"]

    if metric in {"role", "position", "is_core"}:
        actual = player.get(metric)
        if operator == "in":
            return actual in expected
        if operator == "not_in":
            return actual not in expected
        if operator == "==":
            return actual == expected
        if operator == "!=":
            return actual != expected
        return False

    if operator == "rank_is":
        rank = rank_of(context["team"], metric, player.get("id"))
        if rank is None:
            return False
        ranked_count = sum(
            teammate.get(metric) is not None for teammate in context["team"]
        )
        if expected == "first":
            return rank == 0
        if expected == "last":
            return rank == ranked_count - 1
        return rank == expected

    actual = player.get(metric)
    if actual is None:
        return False

    operations = {
        "<": lambda: actual < expected,
        "<=": lambda: actual <= expected,
        ">": lambda: actual > expected,
        ">=": lambda: actual >= expected,
        "==": lambda: actual == expected,
        "!=": lambda: actual != expected,
        "in": lambda: actual in expected,
        "not_in": lambda: actual not in expected,
    }
    operation = operations.get(operator)
    return operation() if operation else False


def _eval_trigger(trigger: Mapping[str, Any] | None, context: Mapping[str, Any]) -> bool:
    if not trigger:
        return True
    if trigger.get("all") and not all(
        _eval_condition(condition, context) for condition in trigger["all"]
    ):
        return False
    return not trigger.get("any") or any(
        _eval_condition(condition, context) for condition in trigger["any"]
    )


def _format_value(metric_name: str, value: Any, metrics: Mapping[str, Any]) -> str:
    if value is None:
        return "—"
    metric = metrics.get(metric_name)
    if not metric:
        return str(value)
    if metric["type"] == "pct":
        return f"{value * 100:.1f}%"
    if metric["type"] == "float":
        return f"{value:.1f}"
    if metric_name == "duration":
        seconds = math.floor(value + 0.5)
        return f"{seconds // 60}:{seconds % 60:02d}"
    if isinstance(value, bool):
        return str(value).lower()
    if metric["type"] == "int":
        return f"{value:,}"
    return str(value)


def _inject(
    text: str | None, context: Mapping[str, Any], metrics: Mapping[str, Any]
) -> str | None:
    if not text:
        return text

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        player = context["player"]
        if key == "player":
            return str(player.get("name") or match.group(0))
        if key == "hero":
            return str(player.get("hero") or match.group(0))
        if key == "n":
            return str(context.get("caseNo") or match.group(0))
        if key == "date":
            return str(context.get("date") or match.group(0))
        if key in {"baseline", "team_avg"}:
            return str(context["baseline"].get(key, match.group(0)))
        value = player.get(key)
        return match.group(0) if value is None else _format_value(key, value, metrics)

    return _PLACEHOLDER.sub(replace, text)


def _hash(value: str) -> int:
    """Match the signed 32-bit hash used by JavaScript bitwise operators."""
    result = 0
    for character in value:
        result = ((result << 5) - result + ord(character)) & 0xFFFFFFFF
        if result >= 0x80000000:
            result -= 0x100000000
    return abs(result)


def _options(opts: Mapping[str, Any] | None, overrides: Mapping[str, Any]) -> dict[str, Any]:
    return {**(dict(opts) if opts else {}), **overrides}


def _verified_entry(entry: Mapping[str, Any], metrics: Mapping[str, Any]) -> bool:
    if entry["severity"] < 2:
        return True
    return all(metrics.get(name, {}).get("verified") is True for name in entry.get("requires", []))


def select(
    db: Mapping[str, Any],
    player: Mapping[str, Any],
    team: Sequence[Mapping[str, Any]],
    opts: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> list[dict[str, Any]]:
    """Select and render evidence for one player."""
    options = _options(opts, overrides)
    mode = options.get("mode", "private")
    contexts = set(options.get("contexts", []))
    tones = set(options.get("tones") or [])
    severity_limit = MODE_LIMITS.get(mode, 3)
    maximum = options.get("max", 4)
    metrics = db["metrics"]
    context = {
        "player": player,
        "team": team,
        "metrics": metrics,
        "caseNo": options.get("caseNo"),
        "date": options.get("date"),
        "baseline": options.get("baseline", {}),
    }

    hits = []
    for entry in db["entries"]:
        if entry["category"] in NON_ACCUSATORY:
            continue
        if not entry.get("trigger") and not options.get("includeFallback"):
            continue
        if entry["severity"] > severity_limit:
            continue
        if tones and not tones.intersection(entry.get("tone", [])):
            continue
        if not _verified_entry(entry, metrics):
            continue
        required = entry.get("requires", [])
        if any(player.get(metric) is None for metric in required):
            continue
        if "data_incomplete" in contexts and any(metric in PARSE_ONLY for metric in required):
            continue
        if contexts.intersection(entry.get("forbidden_context", [])):
            continue
        if _eval_trigger(entry.get("trigger"), context):
            hits.append(entry)

    hits.sort(key=lambda entry: entry["severity"], reverse=True)
    picked = []
    seen = set()
    for entry in hits:
        if entry["category"] in seen:
            continue
        seen.add(entry["category"])
        picked.append(entry)
        if len(picked) >= maximum:
            break

    seed = options.get("seed", player.get("id", 0))
    rendered = []
    for entry in picked:
        required = entry.get("requires", [])
        base_text = entry["text"]
        pool = [base_text] + [
            {**base_text, **variant} for variant in entry.get("variants", [])
        ]
        text = pool[_hash(f"{seed}{entry['id']}") % len(pool)]
        use_safe = mode != "private" and entry["severity"] >= 1
        rendered.append(
            {
                "id": entry["id"],
                "category": entry["category"],
                "severity": entry["severity"],
                "tone": entry["tone"],
                "tag": _inject(text.get("tag"), context, metrics),
                "fact": _inject(text.get("fact"), context, metrics),
                "quip": _inject(
                    text.get("safe") if use_safe else text.get("quip"),
                    context,
                    metrics,
                ),
                "verdict": _inject(text.get("verdict"), context, metrics),
                "share": _inject(text.get("share"), context, metrics),
                "safe": _inject(text.get("safe"), context, metrics),
                "basis": [
                    {
                        "metric": metric,
                        "label": metrics.get(metric, {}).get("label", metric),
                        "value": _format_value(metric, player.get(metric), metrics),
                        "source": metrics.get(metric, {}).get("source"),
                    }
                    for metric in required
                ],
            }
        )
    return rendered


def pick(
    db: Mapping[str, Any],
    category: str,
    opts: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any] | None:
    """Pick one piece of scene copy from a category."""
    options = _options(opts, overrides)
    pool = [entry for entry in db["entries"] if entry["category"] == category]
    if not pool:
        return None
    index = options.get("index")
    if index is None:
        index = _hash(str(options.get("seed", int(time.time() * 1000))))
    entry = pool[index % len(pool)]
    context = {
        "player": {},
        "team": [],
        "metrics": db["metrics"],
        "caseNo": options.get("caseNo"),
        "date": options.get("date"),
        "baseline": {},
    }
    return {
        "id": entry["id"],
        "tag": _inject(entry["text"].get("tag"), context, db["metrics"]),
        "quip": _inject(entry["text"].get("quip"), context, db["metrics"]),
        "share": _inject(entry["text"].get("share"), context, db["metrics"]),
    }


def accuse(
    db: Mapping[str, Any],
    team: Sequence[Mapping[str, Any]],
    opts: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Score every player and return suspects in descending attribution order."""
    options = _options(opts, overrides)
    score_mode = options.get("score_mode", "guilt")
    suspects = []
    for player in team:
        evidence = select(db, player, team, options)
        score = (
            len(evidence)
            if score_mode == "merit"
            else sum(item["severity"] ** 2 for item in evidence)
        )
        suspects.append({"player": player, "evidence": evidence, "score": score})
    if score_mode == "merit":
        suspects.sort(
            key=lambda result: (
                -result["score"],
                -(result["player"].get("damage_share") or 0),
                result["player"].get("gold_share") or 0,
            )
        )
    else:
        suspects.sort(key=lambda result: result["score"], reverse=True)
    return {
        "suspects": suspects,
        "noGuilty": (
            not any(result["score"] > 0 for result in suspects)
            if score_mode == "merit"
            else not any(result["score"] >= 4 for result in suspects)
        ),
    }


_rankOf = rank_of

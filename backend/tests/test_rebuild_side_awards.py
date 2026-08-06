import json

from app.models import Trial
from scripts.rebuild_side_awards import recompute_verdict
from tests.test_award import _case_with_players


def test_recompute_verdict_preserves_payload_and_adds_current_side_award() -> None:
    case, rows = _case_with_players()
    trial = Trial(
        status="closed",
        verdict_player_id=rows[0].player_id,
        verdict_json=json.dumps({"verdict": "原判词", "tally": {"1": 1}}),
        match=case,
    )

    payload = recompute_verdict(trial)

    assert payload is not None
    assert payload["verdict"] == "原判词"
    assert payload["tally"] == {"1": 1}
    assert payload["side_award"]["player_id"] == rows[1].player_id


def test_recompute_verdict_keeps_zero_highlight_award_null() -> None:
    case, rows = _case_with_players()
    for row in rows:
        row.damage_share = 0
        row.tower_damage = 0
        row.teamfight_participation = 0
        row.obs_placed = 0
        row.sen_placed = 0
        row.metrics_json = json.dumps({"kda_ratio": 0, "gold_share": 0.2})
    trial = Trial(status="closed", verdict_json="{}", match=case)

    payload = recompute_verdict(trial)

    assert payload is not None
    assert payload["side_award"] is None

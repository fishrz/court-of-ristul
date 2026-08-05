import json

from app.award import pick_side_award
from app.engine import _verified_entry, accuse, select
from app.models import Match, MatchPlayer, Player
from app.poller import _load_meme_db
from app.verdict_copy import VERDICT_COPY


def _entry(entry_id: str, tone: str, severity: int = 0) -> dict:
    return {
        "id": entry_id,
        "category": entry_id,
        "severity": severity,
        "tone": [tone],
        "requires": [],
        "trigger": {},
        "text": {"tag": entry_id, "quip": entry_id, "verdict": entry_id},
    }


def test_select_filters_entries_by_tone() -> None:
    db = {
        "metrics": {},
        "entries": [_entry("praise", "praise"), _entry("roast", "roast", 2)],
    }
    player = {"id": 1}

    evidence = select(
        db,
        player,
        [player],
        mode="private",
        tones={"praise"},
        includeFallback=True,
    )

    assert [item["id"] for item in evidence] == ["praise"]


def test_merit_score_counts_evidence_and_uses_stable_tiebreaks() -> None:
    db = {"metrics": {}, "entries": [_entry("praise", "praise")]}
    team = [
        {"id": 1, "damage_share": 0.3, "gold_share": 0.25},
        {"id": 2, "damage_share": 0.4, "gold_share": 0.3},
    ]

    result = accuse(
        db,
        team,
        mode="private",
        tones={"praise"},
        includeFallback=True,
        score_mode="merit",
    )

    assert [item["score"] for item in result["suspects"]] == [1, 1]
    assert result["suspects"][0]["player"]["id"] == 2
    assert result["noGuilty"] is False


def _case_with_players() -> tuple[Match, list[MatchPlayer]]:
    case = Match(match_id=99, parse_status="parsed", we_won=False)
    rows = []
    values = [
        (1, "主奖", 0.50, 900, 0.70, 0, 0, 8.0, 0.30),
        (2, "副奖", 0.35, 1200, 0.85, 12, 5, 7.0, 0.18),
        (3, "普通", 0.15, 0, 0.40, 1, 0, 2.0, 0.22),
    ]
    for player_id, name, damage, tower, teamfight, obs, sen, kda, gold in values:
        player = Player(id=player_id, steam_id=player_id, display_name=name)
        rows.append(
            MatchPlayer(
                player_id=player_id,
                player=player,
                hero_id=player_id,
                hero_name=f"英雄{player_id}",
                is_our_team=True,
                damage_share=damage,
                tower_damage=tower,
                teamfight_participation=teamfight,
                obs_placed=obs,
                sen_placed=sen,
                metrics_json=json.dumps({"kda_ratio": kda, "gold_share": gold}),
            )
        )
    case.players = rows
    return case, rows


def test_side_award_uses_five_highlight_dimensions_and_excludes_main_winner() -> None:
    case, rows = _case_with_players()

    award = pick_side_award(case, exclude_player_id=rows[0].player_id)

    assert award is not None
    assert award["player_id"] == rows[1].player_id
    assert award["name"] == "副奖"
    assert award["hero"] == "英雄2"
    assert award["tag"] == "虽败犹荣"
    assert [item["metric"] for item in award["basis"]] == [
        "tower_damage",
        "teamfight_participation",
        "vision",
    ]
    assert "塔伤 1,200" in award["fact"]


def test_side_award_returns_none_when_every_highlight_score_is_zero() -> None:
    case, rows = _case_with_players()
    for row in rows:
        row.damage_share = 0
        row.tower_damage = 0
        row.teamfight_participation = 0
        row.obs_placed = 0
        row.sen_placed = 0
        row.metrics_json = json.dumps({"kda_ratio": 0, "gold_share": 0.2})

    assert pick_side_award(case, exclude_player_id=None) is None


def test_polarity_copy_table_and_placeholder_memes_are_backend_contracts() -> None:
    assert VERDICT_COPY["defeat"]["side_award_name"] == "败方亮点"
    assert VERDICT_COPY["victory"]["side_award_name"] == "甘草功臣"
    assert VERDICT_COPY["victory"]["share_prefix"] == "我们赢了"

    meme_db = _load_meme_db()
    entries = {entry["id"]: entry for entry in meme_db["entries"]}
    placeholder_ids = {
        "victory_generic_contribution",
        "victory_generic_support",
        "consolation_generic_effort",
        "consolation_generic_support",
    }
    assert placeholder_ids <= entries.keys()
    assert all(
        _verified_entry(entries[entry_id], meme_db["metrics"])
        for entry_id in placeholder_ids
    )

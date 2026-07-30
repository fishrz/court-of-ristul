import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.engine import accuse, select


@pytest.fixture(scope="module")
def meme_db() -> dict:
    path = Path(__file__).parents[1] / "data" / "memes.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def team() -> list[dict]:
    path = Path(__file__).parent / "fixtures" / "match_8917764448.json"
    match = json.loads(path.read_text(encoding="utf-8"))
    players = match["players"]
    total_net_worth = sum(player["net_worth"] for player in players)
    total_damage = sum(player["hero_damage"] for player in players)
    for player in players:
        player["gold_share"] = player["net_worth"] / total_net_worth
        player["damage_share"] = player["hero_damage"] / total_damage
        player["kda_ratio"] = (player["kills"] + player["assists"]) / max(
            player["deaths"], 1
        )
        player["duration"] = match["duration"]
    return players


@pytest.fixture(scope="module")
def result(meme_db: dict, team: list[dict]) -> dict:
    return accuse(meme_db, team, mode="private", contexts=["defeat"])


def suspect(result: dict, name: str) -> dict:
    return next(item for item in result["suspects"] if item["player"]["name"] == name)


def test_lina_is_not_accused_for_tp(result: dict) -> None:
    lina = suspect(result, "风希")
    assert not any(
        evidence["category"] == "tp_rotation" and evidence["severity"] >= 1
        for evidence in lina["evidence"]
    )


def test_lina_receives_tp_diligent_praise(result: dict) -> None:
    lina = suspect(result, "风希")
    assert any(evidence["id"] == "tp_diligent" for evidence in lina["evidence"])


def test_lina_is_not_top_suspect(result: dict) -> None:
    assert result["suspects"][0]["player"]["name"] != "风希"


def test_private_mode_top_suspect_is_perennis(result: dict) -> None:
    assert result["suspects"][0]["player"]["name"] == "Perennis"


def test_offlaner_is_accused_for_lane_collapse(result: dict) -> None:
    evidence = suspect(result, "黑刺")["evidence"]
    assert any(item["category"] == "lane" for item in evidence)


def test_offlaner_hits_vision_ward_only(result: dict) -> None:
    evidence = suspect(result, "黑刺")["evidence"]
    assert any(item["id"] == "vision_ward_only" for item in evidence)


def test_scene_copy_never_enters_attribution(result: dict) -> None:
    categories = {
        evidence["category"]
        for item in result["suspects"]
        for evidence in item["evidence"]
    }
    assert categories.isdisjoint({"court", "loading", "share"})


def test_supports_are_not_accused_for_low_early_xp(result: dict) -> None:
    support_evidence = [
        evidence
        for item in result["suspects"]
        if item["player"]["role"] == "support"
        for evidence in item["evidence"]
    ]
    assert not any(item["id"] == "lane_xp_starved" for item in support_evidence)


def test_perennis_is_accused_for_low_teamfight_participation(result: dict) -> None:
    evidence = suspect(result, "Perennis")["evidence"]
    assert any(item["category"] == "teamfight" for item in evidence)


def test_null_metrics_do_not_produce_evidence(meme_db: dict, team: list[dict]) -> None:
    unparsed = deepcopy(team[0])
    unparsed.update(
        id=99,
        name="未解析",
        lh_at_10=None,
        lane_role=None,
        teamfight_participation=None,
        stuns=None,
        obs_placed=None,
        sen_placed=None,
        tp_uses=None,
    )
    evidence = select(
        meme_db,
        unparsed,
        team,
        mode="private",
        contexts=["data_incomplete"],
    )
    guarded = {
        "lh_at_10",
        "lane_role",
        "teamfight_participation",
        "stuns",
        "obs_placed",
        "sen_placed",
        "tp_uses",
    }
    assert not any(
        basis["metric"] in guarded
        for item in evidence
        for basis in item["basis"]
    )


def test_public_mode_has_no_harsh_entries(meme_db: dict, team: list[dict]) -> None:
    perennis = next(player for player in team if player["name"] == "Perennis")
    evidence = select(
        meme_db,
        perennis,
        team,
        mode="public",
        contexts=["defeat"],
    )
    assert not any(item["severity"] >= 2 for item in evidence)


def test_rendered_evidence_is_resolved_and_explainable(
    result: dict, meme_db: dict, team: list[dict]
) -> None:
    evidence = [
        item
        for suspect_result in result["suspects"]
        for item in suspect_result["evidence"]
    ]
    texts = [
        text
        for item in evidence
        for text in (
            item["tag"],
            item["fact"],
            item["quip"],
            item["verdict"],
            item["share"],
        )
        if text
    ]
    assert not any("{" in text or "}" in text for text in texts)
    assert all(item["basis"] for item in evidence if item["severity"] >= 2)

    unverified_db = deepcopy(meme_db)
    unverified_db["metrics"]["deaths"]["verified"] = False
    lina = next(player for player in team if player["name"] == "风希")
    unverified_evidence = select(
        unverified_db,
        lina,
        team,
        mode="private",
        contexts=["defeat"],
    )

    assert not any(
        item["severity"] >= 2
        and any(basis["metric"] == "deaths" for basis in item["basis"])
        for item in unverified_evidence
    )

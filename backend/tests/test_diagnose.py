"""诊断层测试。

重点不是测「代码能跑」，而是测那三条反过度解读的规矩真的生效：
样本不足会闭嘴、中间分位不发言、每条结论都带得出行动。
这几条是产品可信度的地基——一个动不动就瞎解读的教练，
玩家看两次就再也不点开了。
"""

from __future__ import annotations

import pytest

from app import diagnose


def _facts(**kwargs):
    base = {
        "benchmarks": {},
        "deaths": {},
        "items": {"timings": {}},
        "curve": {},
        "hero_id": 48,
        "rank_tier": 73,
    }
    base.update(kwargs)
    return base


class TestBracketLabel:
    def test_divine(self):
        assert diagnose.bracket_label(73) == "超凡3"

    def test_immortal_has_no_stars(self):
        assert diagnose.bracket_label(80) == "不朽"

    def test_unranked(self):
        assert diagnose.bracket_label(None) == "未知分段"


class TestBenchmarks:
    def test_middle_percentiles_stay_silent(self):
        """规矩 2：85 vs 90 分位没有行为含义，不许解读。"""
        facts = _facts(
            benchmarks={
                "gold_per_min": {"pct": 55.0, "raw": 500, "label": "经济"},
                "xp_per_min": {"pct": 62.0, "raw": 600, "label": "经验"},
                "last_hits_per_min": {"pct": 88.0, "raw": 8, "label": "补刀"},
            }
        )
        assert diagnose.diagnose_benchmarks(facts) == []

    def test_low_percentile_reported_with_action(self):
        facts = _facts(
            benchmarks={"gold_per_min": {"pct": 12.0, "raw": 220, "label": "经济"}}
        )
        found = diagnose.diagnose_benchmarks(facts)
        assert len(found) == 1
        assert found[0]["severity"] == "bad"
        # 规矩 3：说不出下一步就不是诊断
        assert found[0]["action"]

    def test_strong_and_weak_produces_insight(self):
        """强弱并存才是有信息量的诊断——证明不是能力问题而是转化问题。"""
        facts = _facts(
            benchmarks={
                "gold_per_min": {"pct": 97.6, "raw": 940, "label": "经济"},
                "tower_damage": {"pct": 20.0, "raw": 1200, "label": "推塔"},
            }
        )
        kinds = {f["kind"] for f in diagnose.diagnose_benchmarks(facts)}
        assert "benchmark_gap" in kinds

    def test_high_only_is_not_a_complaint(self):
        """全是高分位时不该硬挑毛病。"""
        facts = _facts(
            benchmarks={"gold_per_min": {"pct": 98.0, "raw": 940, "label": "经济"}}
        )
        assert diagnose.diagnose_benchmarks(facts) == []


class TestDeaths:
    def test_dead_time_over_threshold(self):
        facts = _facts(deaths={"dead_pct": 14.5, "dead_seconds": 540, "killers": []})
        found = diagnose.diagnose_deaths(facts)
        assert any(f["kind"] == "dead_time" for f in found)

    def test_normal_dead_time_silent(self):
        facts = _facts(deaths={"dead_pct": 7.0, "dead_seconds": 200, "killers": []})
        assert diagnose.diagnose_deaths(facts) == []

    def test_concentrated_killer_flagged(self):
        facts = _facts(
            deaths={
                "dead_pct": 5.0,
                "dead_seconds": 100,
                "killers": [
                    {"hero": "zuus", "count": 5},
                    {"hero": "lich", "count": 2},
                    {"hero": "lina", "count": 1},
                ],
            }
        )
        found = diagnose.diagnose_deaths(facts)
        assert any(f["kind"] == "killer_focus" for f in found)

    def test_spread_deaths_not_flagged_as_focus(self):
        """死亡平均分布说明是团战输了，不是被单点针对——不该误报。"""
        facts = _facts(
            deaths={
                "dead_pct": 5.0,
                "dead_seconds": 100,
                "killers": [
                    {"hero": "zuus", "count": 2},
                    {"hero": "lich", "count": 2},
                    {"hero": "lina", "count": 2},
                ],
            }
        )
        found = diagnose.diagnose_deaths(facts)
        assert not any(f["kind"] == "killer_focus" for f in found)

    def test_few_deaths_never_flagged(self):
        """总共死 3 次，其中 2 次同一个人——样本太小，不算被针对。"""
        facts = _facts(
            deaths={
                "dead_pct": 3.0,
                "dead_seconds": 60,
                "killers": [{"hero": "zuus", "count": 2}, {"hero": "lich", "count": 1}],
            }
        )
        assert diagnose.diagnose_deaths(facts) == []


class TestLane:
    def test_zero_lasthit_minutes_detected(self):
        """累计曲线看不出崩盘，增量能——这是 lh_delta 存在的理由。"""
        facts = _facts(
            curve={
                "lh_delta": [3, 2, 0, 4, 0, 6, 5, 4, 10, 5],
                "lh_t": list(range(40)),
            }
        )
        found = diagnose.diagnose_lane(facts)
        broken = [f for f in found if f["kind"] == "lane_broken"]
        assert broken and broken[0]["zero_minutes"] == [3, 5]

    def test_single_zero_minute_tolerated(self):
        """一分钟没补到刀是常事，不值得上纲上线。"""
        facts = _facts(
            curve={"lh_delta": [3, 2, 3, 4, 0, 6, 5, 4, 10, 5], "lh_t": list(range(40))}
        )
        found = diagnose.diagnose_lane(facts)
        assert not any(f["kind"] == "lane_broken" for f in found)

    def test_tp_scarcity(self):
        facts = _facts(
            items={"timings": {}, "tp_bought": 6},
            curve={"lh_t": list(range(63))},
        )
        found = diagnose.diagnose_lane(facts)
        assert any(f["kind"] == "tp_scarce" for f in found)

    def test_short_match_no_tp_complaint(self):
        """20 分钟的局买 3 个 TP 很正常，不该报。"""
        facts = _facts(
            items={"timings": {}, "tp_bought": 3},
            curve={"lh_t": list(range(20))},
        )
        found = diagnose.diagnose_lane(facts)
        assert not any(f["kind"] == "tp_scarce" for f in found)


class TestTrend:
    def test_insufficient_history_says_nothing(self):
        """规矩 1：4 场不足以谈趋势。"""
        history = [{"dead_pct": 10.0} for _ in range(4)]
        assert diagnose.summarize_trend(history) == []

    def test_worsening_trend_detected(self):
        history = [{"dead_pct": v} for v in (5, 6, 6, 12, 13, 14)]
        out = diagnose.summarize_trend(history)
        assert out and "涨到" in out[0]

    def test_improving_trend_detected(self):
        history = [{"dead_pct": v} for v in (14, 13, 12, 6, 6, 5)]
        out = diagnose.summarize_trend(history)
        assert out and "降到" in out[0]

    def test_noise_not_reported_as_trend(self):
        """1-2 个百分点的波动是噪音，不是退步。"""
        history = [{"dead_pct": v} for v in (9, 10, 9, 10, 11, 10)]
        assert diagnose.summarize_trend(history) == []


class TestFindingShape:
    @pytest.mark.parametrize(
        "facts",
        [
            _facts(benchmarks={"gold_per_min": {"pct": 10.0, "raw": 200, "label": "经济"}}),
            _facts(deaths={"dead_pct": 20.0, "dead_seconds": 600, "killers": []}),
            _facts(curve={"lh_delta": [0, 0, 3, 4, 5, 6, 5, 4, 3, 2], "lh_t": list(range(40))}),
        ],
    )
    def test_every_finding_carries_an_action(self, facts):
        """规矩 3 的总检查：没有 action 的 finding 一律不合格。"""
        found = (
            diagnose.diagnose_benchmarks(facts)
            + diagnose.diagnose_deaths(facts)
            + diagnose.diagnose_lane(facts)
        )
        assert found
        for item in found:
            assert item["action"].strip(), f"{item['kind']} 缺少可执行建议"
            assert item["text"].strip()
            assert item["severity"] in {"bad", "warn", "insight", "mitigating", "info"}

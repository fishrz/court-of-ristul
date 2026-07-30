"""书记官（DeepSeek）接入的单元测试。

这里只测降级与边界，不打真实 API：CI 里不该依赖外部 key 和 ¥。
真实调用已在接入时人工验证过（12.8s / 710 tokens / JSON 可解析）。
"""

import json

import pytest

from app import ai


@pytest.fixture
def players():
    return [
        {"player_id": 11, "name": "风希", "hero": "莉娜", "role": "中单",
         "kills": 9, "deaths": 8, "assists": 9, "gpm": 627,
         "teamfight": 0.69, "damage": 0.278, "lh10": 55, "obs": 0},
        {"player_id": 22, "name": "黑刺", "hero": "巨牙海民", "role": "一号位",
         "kills": 5, "deaths": 8, "assists": 13, "gpm": 293,
         "teamfight": 0.69, "damage": 0.105, "lh10": 1, "obs": 2},
    ]


@pytest.mark.asyncio
async def test_no_key_degrades_silently(monkeypatch, players):
    """没配 key 时必须返回 None 而不是抛异常——法庭不能因书记官请假就开不了庭。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert await ai.judge(we_won=False, duration="36:55", players=players) is None


@pytest.mark.asyncio
async def test_empty_roster_returns_none(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert await ai.judge(we_won=False, duration="36:55", players=[]) is None


def _fake_response(content, finish_reason="stop"):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"finish_reason": finish_reason, "message": {"content": content}}
                ]
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    return _Client


@pytest.mark.asyncio
async def test_happy_path(monkeypatch, players):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    body = json.dumps({"guilty": 2, "reason": "10分钟正补1", "advice": "先练补刀"})
    monkeypatch.setattr(ai.httpx, "AsyncClient", _fake_response(body))
    result = await ai.judge(we_won=False, duration="36:55", players=players)
    assert result == {"guilty": 2, "reason": "10分钟正补1", "advice": "先练补刀"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content, finish_reason",
    [
        ('{"guilty": 9, "reason": "x"}', "stop"),        # 序号越界
        ('{"guilty": 0, "reason": "x"}', "stop"),        # 1-based，0 非法
        ('{"guilty": "2", "reason": "x"}', "stop"),      # 类型不对
        ('{"guilty": 2, "reason": ""}', "stop"),         # 空判词
        ('{"guilty": 2, "reason"', "stop"),              # JSON 截断
        ('{"guilty": 2, "reason": "还没说完', "length"),  # 推理模型 token 耗尽
    ],
)
async def test_bad_output_degrades(monkeypatch, players, content, finish_reason):
    """模型输出任何不可信的形态都必须丢弃，绝不能把半句判词摆上宣判页。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(
        ai.httpx, "AsyncClient", _fake_response(content, finish_reason)
    )
    assert await ai.judge(we_won=False, duration="36:55", players=players) is None


@pytest.mark.asyncio
async def test_network_error_degrades(monkeypatch, players):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(ai.httpx, "AsyncClient", _Boom)
    assert await ai.judge(we_won=False, duration="36:55", players=players) is None


def test_prompt_includes_rule_pick_and_stats(players):
    prompt = ai.build_prompt(
        we_won=False, duration="36:55", players=players, rule_pick=2
    )
    assert "败北" in prompt
    assert "10分钟正补1" in prompt          # 关键罪证必须进 prompt
    assert "2 号最该负责" in prompt          # 规则引擎结论作为参考
    assert "推翻" in prompt                  # 且明确允许推翻


def test_prompt_omits_missing_fields():
    """字段缺失时不能填 0——否则模型会拿假的 0 当罪证定罪。"""
    prompt = ai.build_prompt(
        we_won=True,
        duration="35:12",
        players=[{"name": "甲", "hero": "莉娜", "kills": 1, "deaths": 2, "assists": 3}],
        rule_pick=None,
    )
    assert "GPM" not in prompt
    assert "参团" not in prompt
    assert "胜利" in prompt

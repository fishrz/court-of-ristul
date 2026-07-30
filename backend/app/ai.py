"""DeepSeek 书记官 —— 给规则引擎的归因结论配上真正的判词。

设计取舍（改之前先读）：

1. LLM 不替规则引擎定罪，只做「复核 + 措辞」。
   engine.py 的打分是可解释、可复现、零成本的，是判决的地基；
   LLM 的价值在于判词质量，以及偶尔指出规则引擎漏掉的语境
   （例如「两个一号位」这种阵容异常，纯打分看不出来）。
   因此 LLM 可以给出不同的 guilty，我们如实记录分歧并展示，
   但不会让一次 API 抖动改写整场庭审的结论。

2. 绝不阻塞开庭。deepseek-v4-pro 是推理模型，实测 12.8s/局。
   开庭接口必须立刻返回（五个人同时点开庭），所以这里只提供
   一个可 await 的协程，由调用方丢进 background task，
   完成后通过 WebSocket 广播覆盖判词。投票期 60s，够用。

3. 任何失败都必须静默降级到规则引擎判词。
   没有 key、余额耗尽、超时、返回不是 JSON、guilty 越界——
   一律返回 None，让调用方保留原判词。法庭不能因为
   书记官请假就开不了庭。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

# 推理模型的 completion_tokens 里绝大部分是 reasoning。实测一次判词
# 生成 453 tokens 中有 409 是思考。给 300 会只输出半句就被截断。
MAX_TOKENS = 2000
TIMEOUT = 60.0

SYSTEM_PROMPT = """你是「瑞斯图尔法庭」的书记官——一场 Dota 2 五黑赛后复盘的 AI 法官。

职责：看完整场数据，指出本局最该背锅的那个人，给出一句毒舌但服人的判词，
以及一条真能执行的改进建议。

规矩：
- 判词要毒，但必须踩在数据上。可以刻薄，不能空骂，更不能编造数据里没有的事。
- 说人话，用国服 Dota 玩家的口气，别写成赛事解说稿。
- 赢了的局也照判——赢了也能有人在拖后腿。
- 建议要具体到下一局能做的动作，不要「加强意识」这种废话。
- 只输出 JSON，不要任何额外说明。"""


def _fmt_player(idx: int, p: dict[str, Any]) -> str:
    """把一名玩家压成一行喂给模型。字段缺失一律跳过而不是填 0，
    避免模型拿 0 当真实表现来定罪。"""
    bits = [
        f"{idx} {p.get('name') or '玩家'}",
        p.get("hero") or "",
        p.get("role") or "",
        f"{p.get('kills', 0)}/{p.get('deaths', 0)}/{p.get('assists', 0)}",
    ]
    if p.get("gpm") is not None:
        bits.append(f"GPM{p['gpm']}")
    if p.get("teamfight") is not None:
        bits.append(f"参团{round(p['teamfight'] * 100)}%")
    if p.get("damage") is not None:
        bits.append(f"伤害{round(p['damage'] * 100, 1)}%")
    if p.get("lh10") is not None:
        bits.append(f"10分钟正补{p['lh10']}")
    if p.get("obs") is not None:
        bits.append(f"假眼{p['obs']}")
    return " ".join(b for b in bits if b)


def build_prompt(
    *, we_won: bool, duration: str, players: list[dict[str, Any]], rule_pick: int | None
) -> str:
    lines = [
        f"本局{'胜利' if we_won else '败北'}，时长 {duration}。",
        "我方五人数据：",
        *[_fmt_player(i, p) for i, p in enumerate(players, start=1)],
    ]
    if rule_pick is not None:
        # 告诉它规则引擎的结论，但明确允许推翻——分歧本身是产品看点
        lines.append(
            f"\n本庭归因引擎按指标打分认为 {rule_pick} 号最该负责。"
            "你可以认同，也可以推翻并说明为什么。"
        )
    lines.append(
        '\n输出 JSON：{"guilty":<序号>,"reason":"<25字内判词>",'
        '"advice":"<30字内可执行建议>"}'
    )
    return "\n".join(lines)


async def judge(
    *,
    we_won: bool,
    duration: str,
    players: list[dict[str, Any]],
    rule_pick: int | None = None,
) -> dict[str, Any] | None:
    """返回 {"guilty": 1-based 序号, "reason": str, "advice": str}，失败返回 None。

    players 必须按我方出场顺序排列，序号即 1..N，与 guilty 对应。
    """
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        logger.info("未配置 DEEPSEEK_API_KEY，书记官休假，沿用规则引擎判词")
        return None
    if not players:
        return None

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_prompt(
                    we_won=we_won,
                    duration=duration,
                    players=players,
                    rule_pick=rule_pick,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": MAX_TOKENS,
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as error:  # noqa: BLE001 — 任何异常都降级，不能影响开庭
        logger.warning("DeepSeek 调用失败，沿用规则引擎判词：%s", error)
        return None

    try:
        choice = data["choices"][0]
        # 推理模型在 max_tokens 不够时会 finish_reason=length，此时
        # content 往往是被腰斩的半句 JSON，宁可丢弃也不能展示。
        if choice.get("finish_reason") == "length":
            logger.warning("DeepSeek 输出被 max_tokens 截断，丢弃")
            return None
        result = json.loads(choice["message"]["content"])
    except Exception as error:  # noqa: BLE001
        logger.warning("DeepSeek 返回无法解析，沿用规则引擎判词：%s", error)
        return None

    guilty = result.get("guilty")
    if not isinstance(guilty, int) or not (1 <= guilty <= len(players)):
        logger.warning("DeepSeek 给出的 guilty=%r 越界，丢弃", guilty)
        return None

    reason = str(result.get("reason") or "").strip()
    advice = str(result.get("advice") or "").strip()
    if not reason:
        return None

    return {"guilty": guilty, "reason": reason, "advice": advice}

"""DeepSeek 书记官 —— 给规则引擎的归因结论配上真正的判词。

设计取舍（改之前先读）：

1. LLM 不替规则引擎定罪，只做「复核 + 措辞」。
   engine.py 的打分是可解释、可复现、零成本的，是判决的地基；
   LLM 的价值在于判词质量，以及偶尔指出规则引擎漏掉的语境
   （例如「两个一号位」这种阵容异常，纯打分看不出来）。
   因此 LLM 可以给出不同的 guilty，我们如实记录分歧并展示，
   但不会让一次 API 抖动改写整场庭审的结论。

2. 绝不阻塞开庭。即便 flash 非思考模式实测只要 1.3s，也不在
   请求里等——网络抖一下就是开不了庭。这里只提供一个可 await
   的协程，由调用方丢进 background task，完成后 WebSocket 广播。

4. 判词和教练建议是两种活，用两套配置——这是实测出来的，别合并。

   judge() 判词 —— 非思考模式，1.3s：
       要的是措辞不是推理，归因已经由 engine.py / diagnose.py 做完。
       思考模式反而更差，倾向把数据堆成流水账
       （「GPM940躺尸540秒，BKB晚6分钟，TP仅6个」），
       非思考更像人话（「BKB晚了6分钟还躺尸14%，TP6个是来观光？」）。

   coach() 复盘建议 —— 思考模式，17s：
       要的是因果推理，这里思考模式是碾压性的。实测同一份数据：
       非思考把「推塔伤害60分位」当成一条独立缺点；
       思考模式看穿它是 BKB 晚的下游结果而不是原因，
       并逐条驳掉「TP少」「补刀下滑」等假象，还主动引用了跨局趋势。
       慢没关系——卷宗是私下慢慢看的，不在开庭关键路径上。

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
# 正式版 DeepSeek-V4-Flash-0731。注意 API 只认别名 deepseek-v4-flash，
# 传带日期的 deepseek-v4-flash-0731 会被拒绝。
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# 关掉思考模式。见模块头注释 4：这个任务要的是措辞不是推理。
THINKING_DISABLED = {"type": "disabled"}

# 非思考模式实测整条判词只要 5-60 tokens，但留足余量：
# 万一将来打开思考模式，reasoning 会吃掉绝大部分预算（实测 220/288）。
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
        "thinking": THINKING_DISABLED,
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



# ---- 教练层 ---------------------------------------------------------
# 与 judge() 的区别见模块头注释 4：这里开思考模式，因为要的是
# 因果推理不是措辞。慢（实测 17s）但不在开庭关键路径上——
# 卷宗是私下慢慢看的，可以后台算完再存。

COACH_SYSTEM = """你是 Dota 2 数据教练，服务于「瑞斯图尔法庭」的赛后复盘。

分析纪律：
- 严格区分「结果」和「可控原因」。推塔伤害低可能是结果不是原因，
  别把下游现象当根因开药方。
- 指出因果链，不要罗列现象。玩家自己看得见现象，看不见的是因果。
- 只用给出的数据说话。没给的数据不许脑补，样本量不足就明说。
- 建议必须可量化、下一局能执行。「加强意识」「注意站位」是废话。
- 一次只给一件事。人记不住五条改进意见。

口气：专业但不端着，像开黑群里那个打得最好、愿意教人的朋友。
只输出 JSON，不要额外说明。"""


def _fmt_bench(bench: dict[str, Any]) -> list[str]:
    out = []
    for item in bench.values():
        label = item.get("label") or ""
        raw, pct = item.get("raw"), item.get("pct")
        if pct is None:
            continue
        out.append(f"  {label} {raw} （同英雄 {pct} 分位）")
    return out


async def coach(
    *,
    player_name: str,
    hero: str,
    role: str | None,
    bracket_label: str,
    we_won: bool,
    duration: str,
    facts: dict[str, Any],
    findings: list[dict[str, Any]],
    trend: list[str] | None = None,
) -> dict[str, Any] | None:
    """生成个人复盘。失败返回 None，调用方保留规则层的 findings。

    facts 来自 facts.py，findings 来自 diagnose.py（已经把「BKB 晚 6 分钟」
    这类比较做完了）。LLM 在这里的增量价值是把并列的 findings 串成因果链，
    并识别出哪些是结果、哪些是原因。
    """
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key or not facts:
        return None

    lines = [
        f"玩家：{player_name}，{bracket_label}，{hero}"
        + (f" {role}" if role else "")
        + f"，本局{'胜利' if we_won else '败北'} {duration}",
        "",
    ]

    bench = facts.get("benchmarks") or {}
    if bench:
        lines.append("本局数据 vs 同英雄全服百分位：")
        lines.extend(_fmt_bench(bench))

    deaths = facts.get("deaths") or {}
    if deaths.get("dead_pct") is not None:
        lines.append(
            f"  躺尸 {deaths['dead_seconds']}秒 = 全局 {deaths['dead_pct']}%"
        )
    killers = deaths.get("killers") or []
    if killers:
        kill_str = " ".join(f"{k['hero']}x{k['count']}" for k in killers)
        lines.append(f"  死于：{kill_str}")

    if findings:
        lines.append("")
        lines.append("归因引擎已算出的差距：")
        for item in findings:
            lines.append(f"  {item.get('text')}")

    if trend:
        lines.append("")
        lines.append("跨局趋势：")
        lines.extend(f"  {t}" for t in trend)

    lines.append(
        '\n输出 JSON：{"root_cause":"<一句话根因，必须指出因果链>",'
        '"evidence":["<支撑证据>","<支撑证据>"],'
        '"action":"<下一局唯一要做的一件事，必须可量化>",'
        '"why_not_others":"<为什么其他看起来的问题不是根因>"}'
    )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": COACH_SYSTEM},
            {"role": "user", "content": "\n".join(lines)},
        ],
        "response_format": {"type": "json_object"},
        # 官方上限 384K（context 1M），所以这里远不是天花板。
        # 不顶满是因为 max_tokens 用不到不计费、只影响最坏等待时长——
        # 思考模型真放飞写几万 token，用户在手机上要干等几分钟。
        # 32000 是实测最长思考链（1558）的 20 倍余量，
        # 既不会再像 4000 那样烧穿丢弃，也给失控留了个上界。
        "max_tokens": 32000,
        # flash 默认就是 high，显式写出来是为了让它可见可调：
        # 教练要串因果链，思考深度是核心价值，不降。
        "reasoning_effort": "high",
    }

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                API_URL, json=payload, headers={"Authorization": f"Bearer {key}"}
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as error:  # noqa: BLE001
        logger.warning("DeepSeek 教练调用失败：%s", error)
        return None

    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            # 把用量打出来：不然下次再撞上限，只能靠猜预算该给多少。
            usage = data.get("usage") or {}
            detail = usage.get("completion_tokens_details") or {}
            logger.warning(
                "教练输出被截断，丢弃（prompt=%s completion=%s reasoning=%s 上限=%s）",
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                detail.get("reasoning_tokens"),
                payload["max_tokens"],
            )
            return None
        result = json.loads(choice["message"]["content"])
    except Exception as error:  # noqa: BLE001
        logger.warning("教练返回无法解析：%s", error)
        return None

    root = str(result.get("root_cause") or "").strip()
    action = str(result.get("action") or "").strip()
    if not root or not action:
        return None

    evidence = result.get("evidence")
    return {
        "root_cause": root,
        "action": action,
        "evidence": [str(e) for e in evidence][:4] if isinstance(evidence, list) else [],
        "why_not_others": str(result.get("why_not_others") or "").strip(),
    }

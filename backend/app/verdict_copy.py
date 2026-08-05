"""Single backend source of truth for win/loss verdict terminology."""

from typing import Final

VERDICT_COPY: Final = {
    "defeat": {
        "result_tag": "败",
        "trial_title": "赛后审判",
        "main_award_name": "本局主犯",
        "main_award_cta": "开庭审判",
        "ai_label": "AI 初审",
        "vote_label": "群众判决",
        "appeal_label": "不服上诉",
        "side_award_name": "败方亮点",
        "side_award_tag": "虽败犹荣",
        "share_prefix": "我们输了，但",
    },
    "victory": {
        "result_tag": "胜",
        "trial_title": "赛后表彰",
        "main_award_name": "本局 MVP",
        "main_award_cta": "开庭表彰",
        "ai_label": "AI 提名",
        "vote_label": "群众选择",
        "appeal_label": "补充感言",
        "side_award_name": "甘草功臣",
        "side_award_tag": "不可或缺",
        "share_prefix": "我们赢了",
    },
}


def copy_for(we_won: bool | None) -> dict[str, str]:
    """Return the verdict terminology for the match polarity."""
    return VERDICT_COPY["victory" if we_won else "defeat"]

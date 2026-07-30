#!/usr/bin/env python3
"""
memes.json 校验器 —— 瑞斯图尔法庭词库

用法:
    python3 validate_memes.py            # 校验 memes.json
    python3 validate_memes.py --stats    # 附带统计报表
"""
import json, re, sys, os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "memes.json")

CATEGORIES = {
    "lane", "farm", "teamfight", "objective", "vision", "death",
    "tp_rotation", "carry", "support", "victory", "neutral",
    "court", "loading", "share",
}
TONES = {"court", "fact", "roast", "meme", "self", "praise", "comfort"}
OPS = {"<", "<=", ">", ">=", "==", "!=", "in", "not_in", "rank_is"}
CONTEXTS = {
    "data_incomplete", "victory", "defeat", "public_share",
    "new_player", "early_gg", "mvp",
}
VAR_RE = re.compile(r"\{(\w+)\}")
# 这些变量任何词条都可用，无需在 requires 中声明
FREE_VARS = {"player", "hero", "baseline", "team_avg", "n", "date", "result"}

errors, warnings = [], []


def err(eid, msg):
    errors.append(f"[{eid}] {msg}")


def warn(eid, msg):
    warnings.append(f"[{eid}] {msg}")


def walk_conditions(trig):
    """展开 trigger 里所有叶子条件"""
    out = []
    for key in ("all", "any"):
        for c in trig.get(key, []):
            out.append(c)
    return out


def main():
    if not os.path.exists(PATH):
        print(f"✗ 找不到 {PATH}")
        return 1

    with open(PATH, encoding="utf-8") as f:
        try:
            db = json.load(f)
        except json.JSONDecodeError as e:
            print(f"✗ JSON 解析失败: {e}")
            return 1

    metrics = db.get("metrics", {})
    entries = db.get("entries", [])

    if not metrics:
        errors.append("metrics 为空 —— 没有指标白名单，无法校验 trigger")
    if not entries:
        errors.append("entries 为空")

    # ── metrics 自身完整性 ──
    for name, m in metrics.items():
        for f_ in ("label", "source", "type", "verified"):
            if f_ not in m:
                err(f"metric:{name}", f"缺字段 {f_}")
        if m.get("source", "").startswith("OpenDota purchase_"):
            warn(f"metric:{name}", "使用 purchase_* 字段，确认这是购买量而非使用量")

    seen_ids = set()

    for e in entries:
        eid = e.get("id", "<无id>")

        # 1. id 唯一
        if not e.get("id"):
            errors.append("有词条缺 id")
            continue
        if eid in seen_ids:
            err(eid, "id 重复")
        seen_ids.add(eid)

        # 必填结构
        for f_ in ("category", "severity", "tone", "text"):
            if f_ not in e:
                err(eid, f"缺字段 {f_}")

        cat = e.get("category")
        sev = e.get("severity", 0)
        tones = e.get("tone", [])
        text = e.get("text", {})
        requires = e.get("requires", [])
        trig = e.get("trigger", {})
        forb = e.get("forbidden_context", [])

        # 6. 枚举校验
        if cat and cat not in CATEGORIES:
            err(eid, f"category '{cat}' 不在枚举内")
        if not isinstance(tones, list):
            err(eid, "tone 必须是数组")
        else:
            for t in tones:
                if t not in TONES:
                    err(eid, f"tone '{t}' 不在枚举内")
        for c in forb:
            if c not in CONTEXTS:
                err(eid, f"forbidden_context '{c}' 不在枚举内")
        if sev not in (0, 1, 2, 3):
            err(eid, f"severity {sev} 非法（应为 0-3）")

        # 2. trigger 引用的 metric 必须已定义
        conds = walk_conditions(trig)
        for c in conds:
            mname = c.get("metric")
            op = c.get("op")
            if mname and mname not in metrics and mname not in ("role", "position", "is_core"):
                err(eid, f"trigger 引用未定义指标 '{mname}'")
            if op and op not in OPS:
                err(eid, f"非法运算符 '{op}'")

        # requires 指标必须已定义
        for r in requires:
            if r not in metrics:
                err(eid, f"requires 引用未定义指标 '{r}'")

        # 3. fact 变量必须在 requires 中声明
        fact = text.get("fact", "")
        for v in VAR_RE.findall(fact):
            if v in FREE_VARS:
                continue
            if v not in requires:
                err(eid, f"fact 使用变量 {{{v}}} 但未在 requires 中声明")

        # quip / verdict / share 里的变量也校验
        for field in ("quip", "verdict", "share", "tag"):
            for v in VAR_RE.findall(text.get(field, "")):
                if v in FREE_VARS:
                    continue
                if v not in requires:
                    err(eid, f"{field} 使用变量 {{{v}}} 但未在 requires 中声明")

        # 4. severity>=2 要求全部 requires 已验证
        if sev >= 2:
            if not requires:
                err(eid, f"severity={sev} 但 requires 为空 —— 高severity必须有数据支撑")
            for r in requires:
                m = metrics.get(r, {})
                if not m.get("verified"):
                    err(eid, f"severity={sev} 但指标 '{r}' 未标记 verified")

        # 5. severity>=1 必须有 safe
        if sev >= 1 and not text.get("safe"):
            err(eid, f"severity={sev} 但缺 safe 降级文案")

        # 文案基本非空
        if not text.get("quip") and not text.get("fact") and not text.get("tag"):
            err(eid, "text 全空")

        # 7. 来源可追溯
        src = e.get("source", {})
        if not src.get("raw") and src.get("origin") != "产品自拟":
            warn(eid, "source.raw 为空 —— 无法追溯真实语料")

        # variants 结构
        for i, v in enumerate(e.get("variants", [])):
            if not isinstance(v, dict):
                err(eid, f"variants[{i}] 必须是对象")

    # ── 输出 ──
    print("=" * 60)
    print(f"词条总数: {len(entries)}   指标总数: {len(metrics)}")
    print("=" * 60)

    if errors:
        print(f"\n✗ {len(errors)} 个错误:\n")
        for x in errors:
            print("  " + x)
    if warnings:
        print(f"\n⚠ {len(warnings)} 个警告:\n")
        for x in warnings:
            print("  " + x)
    if not errors and not warnings:
        print("\n✓ 全部通过")
    elif not errors:
        print("\n✓ 无错误")

    if "--stats" in sys.argv and entries:
        print("\n" + "=" * 60)
        print("统计")
        print("=" * 60)

        by_cat = Counter(e.get("category") for e in entries)
        print("\n按分类:")
        for k, v in by_cat.most_common():
            print(f"  {k:14s} {v:3d}  {'▇' * v}")

        by_sev = Counter(e.get("severity") for e in entries)
        names = {0: "neutral", 1: "light", 2: "roast", 3: "brutal"}
        print("\n按严重度:")
        for k in sorted(by_sev):
            print(f"  {k} {names.get(k,''):8s} {by_sev[k]:3d}  {'▇' * by_sev[k]}")

        tone_c = Counter(t for e in entries for t in e.get("tone", []))
        print("\n按语气:")
        for k, v in tone_c.most_common():
            print(f"  {k:10s} {v:3d}")

        n_var = sum(len(e.get("variants", [])) for e in entries)
        print(f"\n变体总数: {n_var}")
        print(f"平均每条变体: {n_var/len(entries):.1f}")

        no_safe = [e["id"] for e in entries
                   if e.get("severity", 0) >= 1 and not e.get("text", {}).get("safe")]
        print(f"缺 safe 文案: {len(no_safe)}")

        # 覆盖检查
        print("\n未覆盖的分类:")
        missing = CATEGORIES - set(by_cat)
        print("  " + (", ".join(sorted(missing)) if missing else "无，全部覆盖"))

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

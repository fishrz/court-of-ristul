# memes.json Schema — 瑞斯图尔法庭词库规范

## 设计原则

**词库是表达层，不是归因引擎。**

```
已验证数据 → 规则命中 → 上下文过滤 → 选模板 → 注入事实变量 → 生成文案
```

绝不允许：先让 AI 造梗 → 再倒找数据。

---

## 顶层结构

```json
{
  "version": "1.0.0",
  "updated": "2026-07-30",
  "meta": {
    "total": 0,
    "sources": ["贴吧", "NGA", "B站", "游戏内语音", "主播口头禅"]
  },
  "metrics": { ...指标定义... },
  "entries": [ ...词条... ]
}
```

---

## metrics —— 指标白名单

**所有 trigger 只能引用这里定义过的指标。** 这是防止 AI 用不存在或口径错误的字段做归因的硬闸门。

```json
"metrics": {
  "lh_at_10": {
    "label": "10分钟正补",
    "source": "OpenDota lh_t[10]",
    "type": "int",
    "verified": true,
    "note": "需 parse 完成；未解析时为 null"
  },
  "tp_uses": {
    "label": "TP使用次数",
    "source": "OpenDota item_uses.tpscroll",
    "type": "int",
    "verified": true,
    "note": "禁止使用 purchase_tpscroll —— 那是购买记录，null≠0"
  }
}
```

每个 metric 必须有：
| 字段 | 说明 |
|---|---|
| `label` | 中文显示名 |
| `source` | 精确到 OpenDota 字段路径 |
| `type` | int / float / pct / bool |
| `verified` | 是否已人工核实过口径。**false 的指标不得用于 severity≥2 的词条** |
| `note` | 陷阱说明，尤其是 null 语义 |

---

## entries —— 词条

```json
{
  "id": "lane_collapse_001",
  "category": "lane",
  "severity": 2,
  "tone": ["roast", "meme"],

  "trigger": {
    "all": [
      { "metric": "lh_at_10", "op": "<=", "value": 3 },
      { "metric": "role", "op": "in", "value": ["core", "mid", "carry"] }
    ]
  },

  "requires": ["lh_at_10"],
  "forbidden_context": ["data_incomplete", "victory", "early_gg"],

  "text": {
    "tag": "对线失守",
    "fact": "10 分钟正补 {lh_at_10}，同位置基准 {baseline}。",
    "quip": "你这补刀，跟没有一样。",
    "verdict": "本庭认定：对线期已提前下班。",
    "safe": "对线期资源获取偏低，建议复盘补刀节奏与站位。"
  },

  "variants": [
    { "quip": "兵是你的仇人吗，一个都不碰。" },
    { "quip": "十分钟三个刀，你是去旅游的？" }
  ],

  "source": {
    "origin": "贴吧",
    "url": "",
    "raw": "你这补刀跟没有一样",
    "note": "游戏内高频嘲讽"
  }
}
```

---

## 字段规范

### severity —— 严重度

| 值 | 名称 | 用途 | 门槛 |
|---|---|---|---|
| 0 | neutral | 中性陈述、数据播报 | 无 |
| 1 | light | 轻损、玩笑 | 至少 1 个 verified 指标 |
| 2 | roast | 中损、正式指控 | 全部 requires 指标 verified |
| 3 | brutal | 重损、判词级 | verified + 私密模式 + 非胜利局 |

**硬规则：`severity >= 2` 时，`requires` 里所有指标必须 `verified: true`。**

### tone —— 语气标签

| 值 | 说明 |
|---|---|
| `court` | 法庭世界观用语（仪式层） |
| `fact` | 纯事实播报 |
| `roast` | 嘲讽 |
| `meme` | 玩梗 |
| `self` | 自嘲（被告视角） |
| `praise` | 正向、表扬 |
| `comfort` | 安慰、台阶 |

### category —— 分类

| 值 | 覆盖 |
|---|---|
| `lane` | 对线、补刀、经验、压制 |
| `farm` | 经济、刷钱、装备、空间 |
| `teamfight` | 参团、开团、技能、站位 |
| `objective` | 推塔、肉山、资源、带线 |
| `vision` | 插眼、排眼、信息差 |
| `death` | 死亡、被抓、连送 |
| `tp_rotation` | TP、支援、转线 |
| `carry` | 核心输出、经济转化 |
| `support` | 辅助贡献、保人 |
| `victory` | MVP、带飞、逆转 |
| `neutral` | 数据不足、全队连坐、休庭 |
| `court` | 法庭通用语 |
| `loading` | 解析、等待、失败状态 |
| `share` | 分享卡标题副标题 |

### trigger —— 触发条件

支持 `all` / `any` 两种组合：

```json
"trigger": { "all": [条件...] }        // 全部满足
"trigger": { "any": [条件...] }        // 任一满足
"trigger": { "all": [...], "any": [...] }  // 都要
```

单条件：
```json
{ "metric": "指标名", "op": "运算符", "value": 值 }
```

运算符：`<` `<=` `>` `>=` `==` `!=` `in` `not_in` `rank_is`

`rank_is` 特殊：比较队内排名，value 为 `"last"` / `"first"` / 数字。

### requires —— 必需指标

列出的指标必须**存在且非 null**，否则整条词条不参与选择。这是防止 null 被当成 0 的核心防线。

### forbidden_context —— 禁用上下文

| 值 | 含义 |
|---|---|
| `data_incomplete` | 比赛未完整解析 |
| `victory` | 本局获胜 |
| `defeat` | 本局失败 |
| `public_share` | 公开分享模式（攻击性过强的不能出现） |
| `new_player` | 新手/低场次玩家 |
| `early_gg` | 早投降局，数据不具代表性 |
| `mvp` | 该玩家是本局 MVP |

### text —— 六层文案

| 层 | 字段 | 特征 |
|---|---|---|
| 事实层 | `fact` | 可审计、带 `{变量}`、冷静精确 |
| 标签层 | `tag` | 4-6 字，数据卡用 |
| 嘴替层 | `quip` | 玩梗、口语、基于事实 |
| 仪式层 | `verdict` | 法庭腔判词 |
| 分享层 | `share` | 短标题，适合截图传播 |
| 缓冲层 | `safe` | 去攻击性版本，公开模式或数据不足时降级用 |

**`safe` 是必填。** 任何 severity≥1 的词条都必须提供无攻击性的替代表达。

### 变量注入

`{metric_name}` 会被替换为该指标的实际值。
`{baseline}` 为同位置分段基准值。
`{player}` `{hero}` 为玩家名与英雄名。

**只有 `requires` 里声明过的指标才能作为变量出现在 `fact` 中。** 构建时会校验。

---

## 选择算法

```
1. 过滤：requires 指标全部存在且非 null
2. 过滤：当前上下文不在 forbidden_context 中
3. 过滤：severity 不超过当前模式上限
       （公开分享模式上限 1，私密五黑上限 3）
4. 匹配：trigger 条件成立
5. 排序：severity 高优先，同级随机
6. 去重：同一玩家同一局不重复使用同 category
7. 变体：从 variants 随机取一条，避免重复观感
```

---

## 校验规则（构建时强制）

| # | 规则 | 失败后果 |
|---|---|---|
| 1 | `id` 全局唯一 | 报错 |
| 2 | `trigger` 引用的 metric 必须在 `metrics` 中定义 | 报错 |
| 3 | `fact` 里的 `{变量}` 必须在 `requires` 中声明 | 报错 |
| 4 | `severity >= 2` 时 requires 全部 `verified: true` | 报错 |
| 5 | `severity >= 1` 必须有 `safe` | 报错 |
| 6 | `category` / `tone` 在枚举内 | 报错 |
| 7 | `source.raw` 非空（可追溯到真实语料） | 警告 |

---

## 禁止事项

- 不得用 `purchase_*` 字段推断使用量
- 不得把 null 当 0
- 不得把猜测性行为（意图、指挥、心态）写成事实
- 不得所有失败局固定判同一人
- 不得在胜利局强行开黑
- 人身攻击（菜、送、演员、代练）不得作为数据分析结论出现

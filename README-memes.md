# 词库系统 · README

## 文件

| 文件 | 作用 |
|---|---|
| `SCHEMA.md` | 词库规范。字段定义、校验规则、禁止事项 |
| `memes.json` | 词库本体。29 个指标 + 63 条词条 |
| `build_memes.py` | 从源码构建 `memes.json`。**改词条改这里，不要直接改 json** |
| `validate_memes.py` | 校验器。7 条硬规则 |
| `memes-engine.js` | 选择引擎。数据 → 文案 |
| `test-engine.js` | 用真实 match 8917764448 跑 12 项回归断言 |
| `inject.js` | 把词库+引擎内联进 `index.html` |

## 工作流

```bash
# 改词条 → 重建 → 校验 → 回归 → 注入
python3 build_memes.py
python3 validate_memes.py --stats
node test-engine.js
node inject.js
```

四步全绿才算改完。`inject.js` 会覆盖 `index.html` 里的 `EVIDENCE` / `NOMINEES`。

## 核心约束

**词库是表达层，不是归因引擎。**

```
已验证数据 → requires闸门 → 上下文过滤 → severity上限 → trigger匹配 → 变量注入 → 文案
```

反向流程（先造梗再找数据）被结构性禁止：`trigger` 只能引用 `metrics` 白名单里的指标，`fact` 里的变量必须在 `requires` 中声明过，校验器会拒绝违规词条。

## 三道防线

### 1. requires 闸门
指标为 `null` 时整条词条不参与选择。这是 Lina 事件的直接产物——当时用 `purchase_tpscroll`（null）推断"全场 0 TP"，实际 `item_uses.tpscroll` 是 13，队内最高。

引擎里对应这行：
```js
if (actual == null) return false;   // null 绝不当 0
```

### 2. severity 分级 + verified 门槛
`severity >= 2` 的词条，其 `requires` 里所有指标必须 `verified: true`。校验器强制。

| severity | 名称 | 门槛 |
|---|---|---|
| 0 | neutral | 无 |
| 1 | light | ≥1 个 verified 指标 |
| 2 | roast | 全部 requires verified |
| 3 | brutal | verified + 私密模式 + 非胜利局 |

### 3. 模式上限
| 模式 | 上限 | 用途 |
|---|---|---|
| `private` | 3 | 熟人五黑，可以放开损 |
| `public` | 1 | 公开分享，自动降级为 `safe` 文案 |
| `safe` | 0 | 纯中性 |

公开模式下 `severity >= 1` 的词条自动用 `safe` 字段替换 `quip`。

## 六层文案

| 层 | 字段 | 例 |
|---|---|---|
| 事实 | `fact` | 全场假眼 3 个，辅助基准 10+。 |
| 标签 | `tag` | 视野真空 |
| 嘴替 | `quip` | 眼呢？插眼了吗？ |
| 仪式 | `verdict` | 本庭认定：未履行视野职责。 |
| 分享 | `share` | 全场 3 眼 |
| 缓冲 | `safe` | 视野布置偏少，建议增加关键眼位。 |

`safe` 对 `severity >= 1` 是必填。

## 语料来源

387 条原始语料，3 路并行检索：贴吧、NGA、B站、知乎、17173、BUFF163、IGXE、dota2.com.cn、游戏内语音轮盘、主播口头禅。

已验证出处的例子：
- 「送！送！送！会不会玩！」— 怒吼天尊 XB 赛场原声
- 「三红变态辣」/「下饭」— dota2.com.cn 术语文 + B站弹幕
- 「这个游戏叫抗压」— YYF，对线被压的美化说法
- 「你玩个大哥就刷钱…要你干嘛呢」— B站查理斯语录
- 「核威慑」— 嘲讽 BurNIng 装备领先但团战没输出
- 「3154」— 震中杯 EG 带盾上高盾消失被团灭
- 「包鸡包眼」— 17173 support 词条原文
- 「假 4 号位」— r/DotA2 中文讨论

自拟文案（court/loading/share 场景层）标记 `origin: "产品自拟"`，校验器不要求 `source.raw`。

## 加新词条

在 `build_memes.py` 里调 `add()`：

```python
add("id_001", "category", severity, ["tone"],
  {"all":[C("metric","<=",value), C("role","in",["core"])]},   # trigger
  ["metric"],                                                    # requires
  {"tag":"标签","fact":"事实 {metric}。","quip":"嘴替",
   "verdict":"判词","safe":"降级文案"},
  [{"quip":"变体1"},{"quip":"变体2"}],                          # variants
  ["data_incomplete","victory"],                                 # forbidden_context
  S("来源","原始语料","备注"))
```

要点：
- 新指标先加进 `METRICS`，标注真实 OpenDota 字段路径和 `verified`
- `role` 白名单要写。辅助的经验/补刀本就低，不加白名单会误判
- `severity >= 2` 必须给 `safe`
- `source.raw` 填真实语料原文，便于日后追溯

## 已知边界

- `role` 字段目前靠外部传入，未从 OpenDota `lane_role` 自动推导
- `baseline` 分段基准值尚未接入，`{baseline}` 变量暂无数据源
- 胜利/MVP 分支只有最小集（1 条），按用户要求延后
- 英雄专属梗（59 条外号语料）尚未入库，需要 hero_id 映射

# 瑞斯图尔法庭 · 后端规格

## 产品定位
Dota 2 五黑赛后复盘仪式。数据为证据，AI 为书记官，玩家实名投票为裁决。
**不是** AI 自动判锅——AI 只整理证据、生成提名和判词，最终由五人投票裁决。

## 用户已确认的四项决策（不可改）

| # | 决策 | 后端含义 |
|---|---|---|
| 1 | **A. 绑 Steam ID 自动发现** | 后台轮询 OpenDota，自动发现新比赛并建案 |
| 2 | **Steam ID 白名单 + 填 ID 页面** | 固定战队，需要一个给朋友填 Steam ID 的页面 |
| 3 | **实时投票** | WebSocket。候审室到齐、60 秒倒计时、实时唱票全部真实同步 |
| 4 | **一局只开庭一次** | `match_id` 唯一约束，判决落库后不可重开 |

## 技术栈（已定，勿改）

```
FastAPI + SQLAlchemy 2.0 (async) + SQLite (aiosqlite)
WebSocket 用 FastAPI 原生（不引 Redis，单机内存广播即可）
httpx 调 OpenDota
pytest + pytest-asyncio
Python 3.11+
```

不引入：Redis、Celery、Postgres、Docker、登录系统（白名单即身份）。

## 数据模型

```python
Player          # 五黑成员白名单
  id, steam_id(unique, 32位), display_name, avatar_url,
  is_active, created_at

Match           # 案卷
  id, match_id(unique, OpenDota), started_at, duration,
  radiant_win, our_side(radiant/dire), we_won,
  parse_status(pending/parsing/parsed/failed),
  raw_json(TEXT),
  evidence_json, nominees_json,     # 归因结果，解析完成时算好（不依赖开庭）
  created_at

MatchPlayer     # 一局里每个成员的表现
  id, match_id(FK), player_id(FK, nullable 敌方),
  hero_id, hero_name, lane_role, is_our_team,
  kills/deaths/assists, gpm, xpm, net_worth,
  lh_at_10, damage_share, teamfight_participation,
  obs_placed, sen_placed, tp_uses, buybacks, stuns, tower_damage,
  metrics_json(TEXT)   # 引擎输入的完整指标包

Trial           # 开庭（一局一次，唯一约束）
  id, match_id(FK, UNIQUE), status(waiting/evidence/voting/closed),
  vote_started_at, vote_deadline,
  verdict_player_id, verdict_json, appeal_text,
  ai_verdict_player_id,             # AI 独立判决（建庭时算好）
  ai_verdict_json,                  # {score, evidence[], reasoning}
  created_at, closed_at

# 注：归因结果（evidence/nominees）存在 Match 上，不是 Trial 上。
# 理由：归因是数据事实，解析完就有；开庭只是走流程。
# 案卷库要能预览未开庭比赛的罪证，所以不能挂在 Trial 下。

Attendance      # 候审室到场
  id, trial_id(FK), player_id(FK), arrived_at
  UNIQUE(trial_id, player_id)

Vote            # 实名投票
  id, trial_id(FK), voter_id(FK), nominee_id(FK), created_at
  UNIQUE(trial_id, voter_id)        # 一人一票，可改票=更新
```

## API

### 白名单
```
GET    /api/players              列出白名单
POST   /api/players              {steam_id, display_name} 加人
DELETE /api/players/{id}         停用（软删，is_active=false）
GET    /api/players/resolve/{steam_id}   查 OpenDota 昵称/头像，供填 ID 页预览
```

### 案卷
```
GET  /api/matches                案卷库。?filter=win|lose|pending
                                 （me 过滤器暂不实现：无登录系统，"我"无法界定。
                                   前端如需高亮自己，靠本地存的 steam_id 客户端过滤）
GET  /api/matches/{match_id}     单案详情（速报 + 罪证 + 提名）
POST /api/matches/sync           手动触发一次 OpenDota 轮询
GET  /api/stats/monthly          门厅统计：开庭/胜诉/大÷ + 大÷榜
```

### 开庭
```
POST /api/trials/{match_id}/open        建庭（已存在则 409，一局只能一次）
GET  /api/trials/{trial_id}             当前状态 + 到场 + 票数
POST /api/trials/{trial_id}/attend      到场签到
POST /api/trials/{trial_id}/start-vote  开始 60 秒投票
POST /api/trials/{trial_id}/vote        {nominee_id} 投票
POST /api/trials/{trial_id}/appeal      {text} 被告最后陈述，≤60 字
```

### WebSocket
```
WS /ws/trials/{trial_id}
```

服务端推送事件：
```json
{"type":"attend",      "player_id":1, "here":3, "total":5}
{"type":"stage",       "stage":"evidence"}
{"type":"vote_start",  "deadline":"2026-07-30T10:00:00Z"}
{"type":"vote",        "voter_id":2, "nominee_id":5, "tally":{"5":2,"7":1}}
{"type":"verdict",     "guilty_player_id":5, "tally":{...}, "ai_verdict_player_id":7, "ai_agrees":false, "verdict":"..."}
{"type":"appeal",      "text":"..."}
```

## 归因引擎接入

**已存在且已验证，不要重写**：
- `memes.json` — 63 条词条 / 29 指标白名单
- `memes-engine.js` — 选择引擎（JS）
- `test-engine.js` — 12 项回归断言，全绿

后端需要一个 Python 等价实现 `app/engine.py`，**必须与 JS 版行为一致**。

### 三条硬约束（这是产品的核心风险，不可妥协）

**1. null ≠ 0**
```python
if actual is None:
    return False   # 指标缺失，整条词条不参与
```
历史事故：曾用 `purchase_tpscroll`（null）推断 Lina「全场 0 TP」，实际 `item_uses.tpscroll = 13`，队内最高。这是产品可信度的生死线。

**2. severity ≥ 2 必须全部指标 verified**
未解析的比赛（`parse_status != parsed`）不得产出重损判词。

**3. 六个 parse-only 指标未解析时不可触发**
```
lh_at_10, lane_role, teamfight_participation, stuns, obs_placed, sen_placed
```

### 回归验证
用 match `8917764448` 作为 fixture，Python 引擎必须复现 JS 版的 12 项断言，重点：
- Lina（风希）**不因 TP 被指控**，且获得正向「支援勤快」
- 黑刺因 `lh_at_10 = 1` 命中「劣单崩盘」
- `court`/`loading`/`share` 三类场景文案**绝不进入玩家归因**
- 辅助不因早期经验低被误判

## OpenDota 接入

```
GET  https://api.opendota.com/api/players/{steam_id}/recentMatches
GET  https://api.opendota.com/api/matches/{match_id}
POST https://api.opendota.com/api/request/{match_id}    # 主动请求解析
```

必须带浏览器 User-Agent，否则 403。限流 + 重试 + 超时降级。
解析通常 ~50 秒，但不能承诺——`parse_status` 状态机 + 前端轮询。

## 轮询任务

FastAPI `lifespan` 里起 asyncio 后台循环：
- 每 5 分钟遍历白名单 `recentMatches`
- 五黑判定：**同一局里我方白名单成员 ≥ 4 人**才建案（4 人也算，避免临时缺一人漏掉）
- 已存在 `match_id` 跳过
- 新案自动 `POST /request/{id}` 触发解析，写 `parse_status=parsing`

## 投票规则

- 实名，可投自己
- 60 秒倒计时，服务端权威（`vote_deadline` 落库，不信客户端）
- 超时未投 = 弃权
- **总票数必须 ≤ 到场人数**，前端展示时总和自洽

## AI 独立判决

AI 的判断**不只是平票裁决，它本身是要展示的内容**。
用户原话：「同时投票完最好有个 AI 选择，这样我们也可以看看 AI 评判的大÷，也保留平票情况下 AI 判决」

- **建庭时**（POST /open）就用引擎算出 `ai_verdict_player_id` 并落库，不依赖投票
- `ai_verdict_json` 存完整证据链和评分，前端要能展示「AI 为什么这么判」
- **结算时**玩家票选和 AI 判决**分别返回**，前端同时展示：「群众判了 X，AI 判了 Y」
- 两者一致/不一致本身就是整活点
- **平票时用 `ai_verdict_player_id` 破局**（用建庭时算好的那个，不重算）

## 验收

```bash
pytest -q          # 全绿
ruff check .       # 干净
```

必须覆盖：
- 引擎 12 项回归（用真实 match fixture）
- 一局只能开庭一次（第二次 409）
- 一人一票（重复投票是改票不是加票）
- 投票总数 ≤ 到场人数
- null 指标不产生指控
- WebSocket 连接 + 广播

## 目录

```
backend/
├── app/
│   ├── main.py          FastAPI + lifespan + 路由挂载
│   ├── models.py        SQLAlchemy
│   ├── schemas.py       Pydantic
│   ├── db.py            async session
│   ├── engine.py        归因引擎（Python 版）
│   ├── opendota.py      API 客户端
│   ├── poller.py        后台轮询
│   ├── ws.py            WebSocket 连接管理
│   └── routers/
│       ├── players.py
│       ├── matches.py
│       └── trials.py
├── tests/
│   ├── conftest.py
│   ├── fixtures/match_8917764448.json
│   ├── test_engine.py
│   ├── test_trials.py
│   └── test_api.py
├── requirements.txt
└── README.md
```

## 前端

前端目前是单文件 `index.html`，前端接线**不在本轮范围**。
本轮只交付后端 + 一个独立的 Steam ID 填写页 `backend/static/join.html`（简单表单，沿用暗红法庭视觉）。

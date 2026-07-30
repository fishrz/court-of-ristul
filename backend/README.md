# 瑞斯图尔法庭 · 后端

Dota 2 五黑赛后复盘仪式。数据为证据，AI 为书记官，玩家实名投票为裁决。

---

## 快速启动

```bash
# 1. 建虚拟环境（WSL 用 python3，系统 Python 遇 PEP 668 加 --break-system-packages）
cd backend
python3 -m venv venv
source venv/bin/activate

# 2. 装依赖
pip install -r requirements.txt

# 3. 起服务
cd ..                      # 回到项目根（backend 的上级目录）
uvicorn backend.app.main:app --reload --port 8000

# 4. 验证
curl http://localhost:8000/health        # {"status":"ok"}
open http://localhost:8000/docs         # Swagger UI
open http://localhost:8000/api/players  # 白名单（初始为空）

# 填 Steam ID 的页面
open http://localhost:8000/join
```

> 数据库文件 `court.db` 在首次启动时自动创建，位置由 `DATABASE_URL` 决定（默认当前目录）。

---

## 配置

全部通过环境变量注入，无配置文件。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./court.db` | 数据库路径 |
| `POLL_INTERVAL_SECONDS` | `300` | 轮询 OpenDota 间隔（秒） |

示例（临时覆盖）：

```bash
DATABASE_URL=sqlite+aiosqlite:////tmp/test.db uvicorn backend.app.main:app --port 8000
```

---

## API 速查表

### 白名单

```
GET    /api/players                         列出所有白名单成员
POST   /api/players                         加人  {"steam_id":"...", "display_name":"..."}
DELETE /api/players/{id}                    软删（is_active=false）
GET    /api/players/resolve/{steam_id}      查 OpenDota 昵称/头像（填 ID 页预览用）
```

```bash
# 示例
curl -X POST http://localhost:8000/api/players \
  -H "Content-Type: application/json" \
  -d '{"steam_id":"76561198xxxxxxxxx","display_name":"Pudge"}'

curl http://localhost:8000/api/players/resolve/76561198xxxxxxxxx
```

### 案卷

```
GET  /api/matches                           案卷库，?filter=win|lose|pending
GET  /api/matches/{match_id}                单案详情（速报 + 罪证 + 提名）
POST /api/matches/sync                      手动触发一次 OpenDota 轮询
GET  /api/stats/monthly                     月统计：开庭/胜诉/大÷ + 大÷榜
```

```bash
curl http://localhost:8000/api/matches?filter=lose
curl -X POST http://localhost:8000/api/matches/sync
```

### 开庭

```
POST /api/trials/{match_id}/open            建庭（一局唯一，已存在返回 409）
GET  /api/trials/{trial_id}                 当前状态 + 到场名单 + 票数
POST /api/trials/{trial_id}/attend          到场签到  {"player_id":1}
POST /api/trials/{trial_id}/start-vote      开始 60 秒投票（需全员到场）
POST /api/trials/{trial_id}/vote            投票  {"nominee_id":3}
POST /api/trials/{trial_id}/appeal          被告陈述  {"text":"..."}（≤60 字）
```

```bash
# 建庭
curl -X POST http://localhost:8000/api/trials/8917764448/open

# 到场
curl -X POST http://localhost:8000/api/trials/1/attend \
  -H "Content-Type: application/json" \
  -d '{"player_id":2}'

# 投票
curl -X POST http://localhost:8000/api/trials/1/vote \
  -H "Content-Type: application/json" \
  -d '{"nominee_id":3}'
```

---

## WebSocket 事件格式

连接地址：`ws://localhost:8000/ws/trials/{trial_id}`

服务端推送五类事件：

```json
// 有人到场
{"type":"attend", "player_id":1, "here":3, "total":5}

// 阶段推进
{"type":"stage", "stage":"evidence"}

// 投票开始（deadline 为 UTC ISO8601）
{"type":"vote_start", "deadline":"2026-07-30T10:00:00Z"}

// 有人投票（tally 是当前票数快照）
{"type":"vote", "voter_id":2, "nominee_id":5, "tally":{"5":2,"7":1}}

// 裁决落定
{"type":"verdict", "guilty_player_id":5, "tally":{"5":3,"7":2},
 "ai_verdict_player_id":7, "ai_agrees":false, "verdict":"...判词..."}

// 被告陈述
{"type":"appeal", "text":"我真的尽力了"}
```

---

## 归因引擎

### 三条硬约束

**1. null ≠ 0**

```python
if actual is None:
    return False   # 指标缺失，整条词条不参与
```

背景：曾用 `purchase_tpscroll`（值为 null）推断 Lina「全场 0 TP」，实际 `item_uses.tpscroll = 13`，队内最高。现在：指标缺失 → 词条整体跳过，不可用缺失值得出负面结论。

**2. severity ≥ 2 必须全部指标已解析**

重损判词（2 级及以上）依赖 `parse_only` 字段，这些字段仅在比赛完整解析后才有值：

```
lh_at_10, lane_role, teamfight_participation, stuns, obs_placed, sen_placed
```

比赛 `parse_status != parsed` 时，上述字段均视为 null，直接卡在约束 1。

**3. court / loading / share 场景文案不进玩家归因**

这三类是 UI 层文本，不挂在任何玩家身上。引擎按 `forbidden_context` 字段过滤。

### 加新词条

词条源文件是项目根的 `build_memes.py`，`memes.json` 是构建产物，不要直接改 json。

```bash
# 1. 改 build_memes.py，在里面调 add()
# 2. 重建
python3 build_memes.py

# 3. 校验（7 条硬规则）
python3 validate_memes.py --stats

# 4. 跑 JS 回归（12 项断言，需要 node）
node test-engine.js

# 全绿才算改完
```

新词条要点：
- 新指标先加进 `METRICS`，标注真实 OpenDota 字段路径和 `verified` 状态
- `role` 白名单要明确写（辅助的经验/补刀本就低，不加会误判）
- `severity >= 2` 的词条必须提供 `safe` 降级文案
- `source.raw` 填真实语料来源，便于日后追溯

---

## 测试

```bash
cd backend
source venv/bin/activate
python -m pytest -q        # 43 个测试，全绿
```

覆盖范围：
- 归因引擎 12 项回归（fixture: match 8917764448）
  - Lina 不因 TP 被指控，且获得「支援勤快」正向归因
  - 黑刺因 `lh_at_10 = 1` 命中「劣单崩盘」
  - court / loading / share 文案不进玩家归因
- 一局只能开庭一次（第二次 409）
- 一人一票（重复投票 = 改票，票数不叠加）
- 投票总数 ≤ 到场人数
- 并发建庭幂等性
- WebSocket 连接与广播
- OpenDota 轮询逻辑与五黑判定（≥4 名白名单成员同局）
- Ruff 静态检查

---

## 已知边界

- **无登录系统**：白名单 = 身份。`steam_id` 由客户端提交，无服务端鉴权。
- **单机内存广播**：WebSocket 连接存在进程内存里，多进程/多机部署时 WebSocket 事件不跨进程同步。现阶段单机跑没有问题。
- **不支持多房间并发开庭**：技术上无限制，但产品设计是「一局一次」，同时开多庭属于未测试场景。
- **投票截止定时器进程内存**：服务重启后，正在投票中的庭审不会自动结算，需要等下一次投票操作触发延迟结算。
- **解析时间不确定**：OpenDota 解析通常 ~50 秒，但无法保证。`parse_status` 状态机 + 前端轮询，不要在前端假设解析已完成。

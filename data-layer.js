/* ============================================================
   瑞斯图尔法庭 · 数据层
   把硬编码 mock 换成真实后端。设计约束：
   - 后端不可达时自动退回内置 mock，页面永不白屏（微信里打不开最致命）
   - 常量名保持 TEAM_DATA / EVIDENCE / NOMINEES / CASES / SEATS 不变，
     渲染函数一行都不用改
   - 后端返回的 nominees 是 {suspects:[{player,evidence,score}]}，
     前端要的是 {nm,hr,score,charge,chips}，在此适配
   ============================================================ */

/* ============================================================
   极性文案表（裁决书 A1）—— 全站唯一定义点
   胜负不切换流程，只切换文案。任何一处再写死「本局大÷」「虽败犹荣」
   都算回归：业务逻辑里这些字面量只应出现在这张表里。
   ============================================================ */
const COPY = Object.freeze({
  guilt: Object.freeze({
    polarity: "guilt",
    waitTitle: "候 审 室",
    waitEn: "The Antechamber",
    waitLead: "卷宗已备妥，传五人到庭",
    briefBig: "败北",
    briefRosterLabel: "阵亡名单",
    briefCount: n => n + " 名被告",
    evTitle: "举 证 阶 段",
    evEn: "Presentation of Evidence",
    evSection: "罪证",
    evCount: n => "共 " + n + " 项",
    evBtn: "举证完毕 · 查看提名",
    voteTitle: "合 议 投 票",
    voteEn: "The Vote",
    voteSection: "本局大÷ · 提名",
    voteHint: "请先选择一名被告",
    votePick: nm => "投给 " + nm + " · 落 槌",
    verdictPre: "本局判决",
    verdictAward: "本局大÷",
    verdictSection: "判词",
    adviceSection: "判后教育",
    adviceNote: "真心的",
    appealSection: "最后陈述",
    appealWho: nm => "被告 " + (nm || ""),
    appealPlaceholder: "给自己辩护一句，60 字以内。说得好可以进判决卡。",
    appealBtn: "提 交 辩 词",
    sideSection: "安慰奖",
    sideTag: "虽败犹荣",
    listAward: "本局大÷",
    caseRes: "败北",
    noneVerdict: "「证据不足，本庭择日再审。」",
    noneName: "无人被判"
  }),
  merit: Object.freeze({
    polarity: "merit",
    waitTitle: "集 结 室",
    waitEn: "The Assembly",
    waitLead: "战报已备妥，传五人受勋",
    briefBig: "胜诉",
    briefRosterLabel: "出战名单",
    briefCount: n => n + " 名功臣",
    evTitle: "表 功 阶 段",
    evEn: "Presentation of Merit",
    evSection: "战功",
    evCount: n => "共 " + n + " 项",
    evBtn: "表功完毕 · 查看提名",
    voteTitle: "合 议 表 决",
    voteEn: "The Commendation",
    voteSection: "本局 MVP · 提名",
    voteHint: "请先选择一名功臣",
    votePick: nm => "推举 " + nm + " · 落 槌",
    verdictPre: "本局表彰",
    verdictAward: "本局 MVP",
    verdictSection: "颂词",
    adviceSection: "再接再厉",
    adviceNote: "也是真心的",
    appealSection: "获奖感言",
    appealWho: nm => "功臣 " + (nm || ""),
    appealPlaceholder: "说两句获奖感言，60 字以内。说得好可以进表彰卡。",
    appealBtn: "呈 上 感 言",
    sideSection: "最佳配角",
    sideTag: "甘草功臣",
    listAward: "本局 MVP",
    caseRes: "胜诉",
    noneVerdict: "「诸位表现均衡，本庭不另作推举。」",
    noneName: "无人获推"
  })
});
/** 当前案卷的极性文案。we_won 未知时按败局兜底（现有全部素材都是败局口径）。 */
function copyOf(weWon) { return weWon ? COPY.merit : COPY.guilt; }
window.COPY = COPY;
window.copyOf = copyOf;

const API = (function () {
  // 生产（同源部署）走相对路径，由 Caddy 反代到后端。
  // 本地开发：静态预览端口和后端端口不同，必须显式打到 8010。
  // 判据用「回环地址且不是 8010 本身」，不要写死某个静态端口号——
  // 换个预览端口就会静默落到 /api 分支，请求全 404。
  const o = location.origin;
  const isLoopback = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|$)/.test(o);
  if (o.startsWith("http") && !isLoopback) return "/api";
  if (isLoopback && o.includes(":8010")) return "/api";
  return "http://127.0.0.1:8010/api";
})();

let LIVE = false; // 是否成功连上后端

async function api(path, opts) {
  const res = await fetch(API + path, Object.assign({
    headers: { "Content-Type": "application/json" }
  }, opts || {}));
  if (!res.ok) throw new Error(path + " -> " + res.status);
  return res.json();
}

/* ---------- 适配器：后端结构 → 前端渲染结构 ---------- */

const POS_CN = {
  carry: "一号位", mid: "中单", offlane: "三号位",
  soft_support: "四号位", hard_support: "五号位",
  support: "辅助", core: "核心位"
};

function fmtDur(sec) {
  if (sec == null) return "--:--";
  const m = Math.floor(sec / 60), s = sec % 60;
  return m + ":" + String(s).padStart(2, "0");
}

function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 3600) return Math.max(1, Math.floor(diff / 60)) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (d.toDateString() === y.toDateString())
    return "昨天 " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  return (d.getMonth() + 1) + "月" + d.getDate() + "日";
}

function fmtDay(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  if (d.toDateString() === now.toDateString()) return "今天";
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "昨天";
  return (d.getMonth() + 1) + "月" + d.getDate() + "日";
}

// 案卷库列表项
function toCase(m) {
  const pending = m.parse_status === "pending" || m.parse_status === "parsing";
  const cp = copyOf(m.we_won);
  return {
    id: String(m.match_id),
    res: pending ? "pending" : (m.we_won ? "win" : "lose"),
    we_won: !!m.we_won,
    // 入口路由表（裁决书 C 节）要的三个字段，后端 MatchListItem 下发
    trial_status: m.trial_status ?? null,
    trial_id: m.trial_id ?? null,
    parse_status: m.parse_status,
    dur: fmtDur(m.duration),
    when: fmtWhen(m.started_at),
    day: fmtDay(m.started_at),
    award: pending ? "待开庭"
      : (m.verdict_name ? cp.listAward : "未宣判"),
    who: m.verdict_name || "",
    // 已解析但尚未开庭 -> 没有判决，不能留空白说明栏
    note: pending
      ? "卷宗已备妥 · 等五人到齐"
      : (m.verdict_note || (m.verdict_name ? "" : "尚未开庭 · 证据待质证")),
    me: false,
    heroes: (m.heroes || []).join(" · ")
  };
}

/* 胜局不得出现 roast 文案（裁决书 F.1，一票否决项）。
   后端 engine 已按 tones 过滤，但历史比赛的 evidence_json / nominees_json
   是建案当时算好的快照，里面仍混着 severity>0 的指控词条。
   前端因此再加一道闸：胜局只放行 severity==0 的表功项。
   宁可这一屏是空的，也不能在表彰页上写「死亡 9 次，队内垫底」。
   等后端把历史快照重算干净后，这道闸留着也无害。 */
function meritSafe(entries, weWon) {
  if (!weWon) return entries;
  return (entries || []).filter(e => !(e.severity > 0));
}

// 举证项：后端 evidence 是 {playerKey: [entry,...]}
function toEvidence(detail, weWon) {
  const byPlayer = detail.evidence || {};
  // evidence 的 key 是引擎侧 player.id；名字/英雄要从 nominees.suspects 取，
  // detail.players 用的是 DB 主键，两者不是同一套 id。
  const nameOf = {};
  ((detail.nominees && detail.nominees.suspects) || []).forEach(s => {
    if (s.player) nameOf[String(s.player.id)] = s.player;
  });

  const out = [];
  Object.keys(byPlayer).forEach(key => {
    meritSafe(byPlayer[key], weWon).forEach(e => {
      const p = nameOf[key] || {};
      const who = (p.name || key) + (p.hero ? " · " + p.hero : "");
      out.push({
        tag: e.tag, who: who, fact: e.fact, quip: e.quip,
        id: e.id,
        // basis 是 [{metric,label,value,source}]，渲染成 "假眼数 3"
        basis: (e.basis || []).map(b => b.label + " " + b.value),
        severity: e.severity || 0
      });
    });
  });
  // 严重度降序，最多 4 项（和原型一致）
  return out.sort((a, b) => b.severity - a.severity).slice(0, 4);
}

// 提名：后端 {suspects:[{player,evidence,score}]} → {nm,hr,score,charge,chips}
// 注意 suspects[].player.id 的口径不唯一（与后端 _ai_verdict 同一个坑）：
//   - 轮询入库的比赛：它是 OpenDota account_id，等于 players.steam_id
//   - 以归一化 fixture 直接喂 engine 的：它只是 1..5 的出场序号
// 投票接口要的是数据库 players.id，所以两种口径都要能换算，
// 只按序号换会让真实比赛的 player_id 全为 null，整场没人可投。
function toNominees(detail, weWon) {
  const sus = (detail.nominees && detail.nominees.suspects) || [];
  // 先按极性过滤每人的证据，过滤后没料的人不该出现在提名里
  const scrubbed = sus
    .map(s => Object.assign({}, s, { evidence: meritSafe(s.evidence, weWon) }))
    .filter(s => s.evidence && s.evidence.length);
  const top = scrubbed.slice(0, 3);
  if (!top.length) return [];
  const ours = (detail.players || []).filter(p => p.is_our_team);
  // 口径 A：steam_id（= OpenDota account_id）→ 数据库 player_id
  const bySteam = {};
  ours.forEach(p => {
    let sid = p.steam_id;
    if (sid == null) {
      try { sid = JSON.parse(p.metrics_json || "{}").account_id; } catch (e) {}
    }
    if (sid != null && p.player_id != null) bySteam[String(sid)] = p.player_id;
  });
  // 口径 B：我方出场顺序 → 数据库 player_id（与后端 ordinal fallback 一致）
  const ordinalToDbId = ours.map(p => p.player_id);
  const resolve = raw => {
    if (raw == null) return null;
    const hit = bySteam[String(raw)];
    if (hit != null) return hit;
    const n = Number(raw);
    if (Number.isInteger(n) && n >= 1 && n <= ordinalToDbId.length)
      return ordinalToDbId[n - 1] ?? null;
    return null;
  };
  const max = Math.max.apply(null, top.map(s => s.score)) || 1;
  return top.map(s => {
    const p = s.player || {};
    const pos = POS_CN[p.role] || p.role || "";
    return {
      nm: p.name,
      hr: (p.hero || "") + (pos ? " · " + pos : ""),
      player_id: resolve(p.id),
      score: Math.round((s.score / max) * 100),
      charge: s.evidence.map(e => e.fact).join(" "),
      // 多条罪证可能引用同一指标（如"假眼数 3"），去重后最多 4 枚
      chips: Object.values(s.evidence.reduce((acc, e) => {
        (e.basis || []).forEach(b => {
          const label = b.label + " " + b.value;
          const hot = e.severity >= 2 ? 1 : 0;
          if (!acc[label] || hot > acc[label][1]) acc[label] = [label, hot];
        });
        return acc;
      }, {})).slice(0, 4)
    };
  });
}

/* ---------- 载入 ---------- */

async function loadArchive() {
  try {
    const list = await api("/matches");
    LIVE = true;
    // 无条件覆盖：CASES 已不再有内置 mock，后端返回空就是真的空。
    CASES = list.map(toCase);
    markMyCases();
  } catch (err) {
    LIVE = false;
    console.warn("[法庭] 案卷库读取失败：", err.message);
  }
}

/* ---------- 身份：CURRENT_PLAYER_ID（裁决书 G.3） ----------
   入口路由第 4/5 条要判断「我是不是当事人」，投票要真实 voter_id。
   没登记就是 null —— 不假装是别人。

   解析链（后端 GET /api/players 的 PlayerOption 只有 id/display_name/avatar_url，
   不含 steam_id，所以不能直接按 steam_id 匹配）：
     1. localStorage 缓存的 cor.player_id —— 命中就不再打网络
     2. GET /api/players/resolve/{steam_id} 拿到 display_name，再在名册里按名字对上
        （该接口走 OpenDota，慢且可能失败，所以结果要缓存）
   后端若日后把 steam_id 加进 PlayerOption，第 2 步可以整段删掉。 */
let CURRENT_PLAYER_ID = null;
let PLAYERS = [];

async function loadPlayers() {
  try {
    PLAYERS = await api("/players");
  } catch (err) {
    PLAYERS = [];
    return null;
  }
  window.__PLAYERS = PLAYERS;

  let sid = null, cached = null;
  try {
    sid = localStorage.getItem("cor.steam_id");
    cached = localStorage.getItem("cor.player_id");
  } catch (e) {}
  if (!sid) { CURRENT_PLAYER_ID = null; window.CURRENT_PLAYER_ID = null; return null; }

  // 1. 缓存命中且这个 id 还在名册里（有人被停用就作废重解析）
  if (cached && PLAYERS.some(p => String(p.id) === String(cached))) {
    CURRENT_PLAYER_ID = Number(cached);
    window.CURRENT_PLAYER_ID = CURRENT_PLAYER_ID;
    return CURRENT_PLAYER_ID;
  }

  // 2. 按昵称回填。resolve 失败就保持 null，不猜。
  try {
    const me = await api("/players/resolve/" + encodeURIComponent(sid));
    const hit = PLAYERS.find(p => p.display_name === me.display_name);
    if (hit) {
      CURRENT_PLAYER_ID = hit.id;
      try { localStorage.setItem("cor.player_id", String(hit.id)); } catch (e) {}
    }
  } catch (err) {
    console.warn("[法庭] 身份解析失败，本次仅能旁听：", err.message);
  }
  window.CURRENT_PLAYER_ID = CURRENT_PLAYER_ID;
  return CURRENT_PLAYER_ID;
}

/** 我是否是这局的当事人。detail 缺席时退回列表项缓存的 our_player_ids。 */
function amInCase(c, detail) {
  if (CURRENT_PLAYER_ID == null) return false;
  const src = (detail && detail.players) || (c && c._players) || [];
  return src.some(p => p.is_our_team && p.player_id === CURRENT_PLAYER_ID);
}

/** 案卷库「我被判」筛选：判决人是我。 */
function markMyCases() {
  if (CURRENT_PLAYER_ID == null) return;
  const me = PLAYERS.find(p => p.id === CURRENT_PLAYER_ID);
  if (!me) return;
  CASES.forEach(c => { c.me = !!c.who && c.who === me.display_name; });
}
window.loadPlayers = loadPlayers;
window.amInCase = amInCase;

async function loadMatch(matchId) {
  try {
    const detail = await api("/matches/" + matchId);
    LIVE = true;
    // 路由表第 4/5 条要用到「我在不在这局」，把 players 挂回案卷缓存
    const c = (typeof CASES !== "undefined" ? CASES : []).find(x => String(x.id) === String(matchId));
    if (c) c._players = detail.players || [];
    const ev = toEvidence(detail, detail.we_won);
    const nom = toNominees(detail, detail.we_won);
    // 真实模式下无条件覆盖：胜局若沿用上一局（败局）的罪证，
    // 就是裁决书 F.1 的一票否决项 —— 宁可空，不可串味。
    EVIDENCE = ev;
    NOMINEES = nom;
    if (detail.players && detail.players.length) {
      // players 行没有 display_name，玩家名在 metrics_json 里
      SEATS = detail.players.filter(p => p.is_our_team).map((p, i) => {
        let nm = "";
        try { nm = (JSON.parse(p.metrics_json || "{}").name) || ""; } catch (e) {}
        return {
          n: nm || ("玩家" + (i + 1)),
          h: p.hero_name || "",
          // 数据库 players.id，attend / 到场广播都靠它对号入座
          player_id: p.player_id ?? null,
          // 真实模式下没有人预先在场：到庭一律由服务端 attend 事件点亮。
          // 不要在这里塞 here:true 或假延迟 d，否则一个人进候审室会看到全员自动就位。
          here: false
        };
      });
    }
    return detail;
  } catch (err) {
    LIVE = false;
    console.warn("[法庭] 卷宗读取失败，使用内置数据：", err.message);
    return null;
  }
}

async function loadStats() {
  try {
    const s = await api("/stats/monthly");
    LIVE = true;
    return s;
  } catch (err) {
    LIVE = false;
    return null;
  }
}

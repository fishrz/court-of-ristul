/* ============================================================
   瑞斯图尔法庭 · 数据层
   把硬编码 mock 换成真实后端。设计约束：
   - 后端不可达时自动退回内置 mock，页面永不白屏（微信里打不开最致命）
   - 常量名保持 TEAM_DATA / EVIDENCE / NOMINEES / CASES / SEATS 不变，
     渲染函数一行都不用改
   - 后端返回的 nominees 是 {suspects:[{player,evidence,score}]}，
     前端要的是 {nm,hr,score,charge,chips}，在此适配
   ============================================================ */

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
  support: "五号位", hard_support: "五号位", core: "核心位"
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
  return {
    id: String(m.match_id),
    res: pending ? "pending" : (m.we_won ? "win" : "lose"),
    dur: fmtDur(m.duration),
    when: fmtWhen(m.started_at),
    day: fmtDay(m.started_at),
    award: pending ? "待开庭"
      : (m.verdict_name ? (m.we_won ? "本局 MVP" : "本局大÷") : "未宣判"),
    who: m.verdict_name || "",
    // 已解析但尚未开庭 -> 没有判决，不能留空白说明栏
    note: pending
      ? "卷宗已备妥 · 等五人到齐"
      : (m.verdict_note || (m.verdict_name ? "" : "尚未开庭 · 证据待质证")),
    me: false,
    heroes: (m.heroes || []).join(" · ")
  };
}

// 举证项：后端 evidence 是 {playerKey: [entry,...]}
function toEvidence(detail) {
  const byPlayer = detail.evidence || {};
  // evidence 的 key 是引擎侧 player.id；名字/英雄要从 nominees.suspects 取，
  // detail.players 用的是 DB 主键，两者不是同一套 id。
  const nameOf = {};
  ((detail.nominees && detail.nominees.suspects) || []).forEach(s => {
    if (s.player) nameOf[String(s.player.id)] = s.player;
  });

  const out = [];
  Object.keys(byPlayer).forEach(key => {
    (byPlayer[key] || []).forEach(e => {
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
// 注意：suspects[].player.id 是【引擎归一化序号 1..n】，不是数据库 players.id。
// 投票接口要的是数据库 id，因此这里必须按我方出场顺序换算成 MatchPlayer.player_id。
function toNominees(detail) {
  const sus = (detail.nominees && detail.nominees.suspects) || [];
  const top = sus.filter(s => s.evidence && s.evidence.length).slice(0, 3);
  if (!top.length) return [];
  // 我方出场顺序 → 数据库 player_id，与后端 ordinal fallback 同一口径
  const ordinalToDbId = (detail.players || [])
    .filter(p => p.is_our_team)
    .map(p => p.player_id);
  const max = Math.max.apply(null, top.map(s => s.score)) || 1;
  return top.map(s => {
    const p = s.player || {};
    const pos = POS_CN[p.role] || p.role || "";
    return {
      nm: p.name,
      hr: (p.hero || "") + (pos ? " · " + pos : ""),
      player_id: ordinalToDbId[(p.id || 0) - 1] ?? null,
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
    if (list.length) CASES = list.map(toCase);
  } catch (err) {
    LIVE = false;
    console.warn("[法庭] 后端不可达，使用内置卷宗：", err.message);
  }
}

async function loadMatch(matchId) {
  try {
    const detail = await api("/matches/" + matchId);
    LIVE = true;
    const ev = toEvidence(detail);
    const nom = toNominees(detail);
    if (ev.length) EVIDENCE = ev;
    if (nom.length) NOMINEES = nom;
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

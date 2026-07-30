/* ============================================================
   瑞斯图尔法庭 · 审判状态桥
   把后端事件接到既有 UI，并保留「后端不可达时用本地模拟」的能力。
   设计原则：WS 只提速，HTTP 才是权威事实来源。
   ============================================================ */

const Trial = {
  id: null,
  matchId: null,
  me: null,          // 我的 player_id
  players: [],       // [{id, steam_id, display_name}]
  status: null,
  deadline: null,    // 服务端权威截止时间（Date）
  tally: {},
  simulated: false   // true = 后端不可用，走原本地演示流程
};
// 显式挂到 window：index.html 的内联脚本先于本文件执行，
// 顶层 const 不会成为 window 属性，内联里的 window.Trial 会取不到。
window.Trial = Trial;
window.castVoteLive = castVoteLive;

function setLiveBadge(on) {
  const el = document.getElementById("liveDot");
  if (!el) return;
  el.classList.toggle("on", !!on);
  el.title = on ? "实时连接正常" : "连接中断，正在重试";
}

/* ---------- 身份：join.html 存的是 steam_id，这里换成 player_id ---------- */
async function resolveMe() {
  let steamId = null;
  try { steamId = localStorage.getItem("cor.steam_id"); } catch (e) {}
  try {
    Trial.players = await api("/players");
  } catch (err) {
    return null;
  }
  if (steamId) {
    const hit = Trial.players.find(p => String(p.steam_id) === String(steamId));
    if (hit) { Trial.me = hit.id; return hit.id; }
  }
  // 没登记过：不假装是别人，返回 null，由调用方引导去 /join
  return null;
}

function nameOfPlayer(playerId) {
  const p = Trial.players.find(x => x.id === playerId);
  return p ? p.display_name : ("玩家" + playerId);
}

/* ---------- 开庭 ---------- */
async function openTrial(matchId) {
  Trial.matchId = matchId;
  await resolveMe();
  try {
    const t = await api("/trials/" + matchId + "/open", { method: "POST", body: "{}" });
    Trial.id = t.id;
    Trial.simulated = false;
  } catch (err) {
    // 已开过庭：后端返回 409，改为读取既有审判
    console.warn("[法庭] 开庭失败，尝试读取既有卷宗：", err.message);
    Trial.simulated = true;
    return null;
  }
  Live.connect(Trial.id);
  await syncTrialState(Trial.id);
  return Trial.id;
}

/* ---------- 权威状态同步（重连 / 回前台 / 首次进入都走它） ---------- */
async function syncTrialState(trialId) {
  let t;
  try {
    t = await api("/trials/" + trialId);
  } catch (err) {
    return null;
  }
  Trial.status = t.status;
  Trial.tally = t.tally || {};
  Trial.deadline = t.vote_deadline ? new Date(t.vote_deadline) : null;

  // 到场情况回填到候审室
  // 后端 attendances 是扁平的 player_id 数组，不是对象数组
  const here = new Set(t.attendances || []);
  SEATS.forEach((s, i) => {
    const pid = s.player_id;
    if (pid != null) s.here = here.has(pid);
    else if (i === 0) s.here = true;
  });
  if (document.querySelector("#sW.on")) {
    renderSeatStates();
    updateWaitCount();
  }
  // 状态机对齐：服务端说到哪一步，前端就跳到哪一步
  if (t.status === "voting" && Trial.deadline) {
    if (!document.querySelector("#s4.on")) go(4);
    startServerCountdown(Trial.deadline);
    renderTally(Trial.tally);
  } else if (t.status === "closed") {
    if (!document.querySelector("#s5.on")) {
      renderVerdict({
        guilty_player_id: t.verdict_player_id,
        tally: Trial.tally,
        ai_verdict_player_id: t.ai_verdict_player_id,
        ai_agrees: t.verdict_player_id === t.ai_verdict_player_id
      });
      go(5);
    }
  }
  return t;
}

/* ---------- 事件分发 ---------- */
function handleLiveEvent(ev) {
  if (!ev || !ev.type) return;
  switch (ev.type) {
    case "attend": {
      const i = SEATS.findIndex(s => s.player_id === ev.player_id);
      if (i >= 0) arrive(i);
      break;
    }
    case "stage":
      if (ev.stage === "evidence" && !document.querySelector("#s3.on")) {
        renderEvidence();
        go(3);
      }
      break;
    case "vote_start":
      Trial.deadline = new Date(ev.deadline);
      if (!document.querySelector("#s4.on")) go(4);
      startServerCountdown(Trial.deadline);
      break;
    case "vote":
      Trial.tally = ev.tally || {};
      renderTally(Trial.tally);
      break;
    case "appeal":
      showAppealText(ev.text);
      break;
    case "verdict":
      Trial.status = "closed";
      renderVerdict(ev);
      go(5);
      break;
  }
}

/* ---------- 服务端权威倒计时（不信任本地 60 秒） ---------- */
function startServerCountdown(deadline) {
  clearInterval(timerId);
  const num = document.getElementById("tSec");
  const fill = document.getElementById("tFill");
  const total = 60;
  const tick = () => {
    const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    if (num) num.textContent = left;
    if (fill) fill.style.width = Math.max(0, Math.min(100, (left / total) * 100)) + "%";
    if (left <= 0) {
      clearInterval(timerId);
      // 不本地结算：等服务端 verdict 事件，避免各端算出不同结果
      const b = document.getElementById("voteBtn");
      if (b) { b.disabled = true; b.textContent = "等待宣判…"; }
    }
  };
  tick();
  timerId = setInterval(tick, 250);
}

/* ---------- 真实投票 ---------- */
async function castVoteLive() {
  if (selected === null) return;
  const nominee = NOMINEES[selected];
  const b = document.getElementById("voteBtn");
  if (b) { b.disabled = true; b.textContent = "唱票中…"; }
  try {
    await api("/trials/" + Trial.id + "/vote", {
      method: "POST",
      body: JSON.stringify({ voter_id: Trial.me, nominee_id: nominee.player_id })
    });
  } catch (err) {
    if (b) { b.disabled = false; b.textContent = "投票失败 · 重试"; }
    console.warn("[法庭] 投票失败：", err.message);
  }
}

/* ---------- 实时票数渲染 ---------- */
function renderTally(tally) {
  const nodes = document.querySelectorAll(".nom");
  NOMINEES.forEach((n, i) => {
    const box = nodes[i] && nodes[i].querySelector(".tally");
    if (!box) return;
    const count = tally[String(n.player_id)] || 0;
    box.innerHTML = "";
    for (let k = 0; k < count; k++) {
      const a = document.createElement("div");
      a.className = "av";
      a.textContent = "•";
      box.appendChild(a);
    }
  });
}

function showAppealText(text) {
  const el = document.getElementById("appealShown");
  if (el && text) { el.textContent = text; el.style.display = ""; }
}

/* ---------- 座位状态刷新（不重启模拟定时器，纯状态回填） ---------- */
function renderSeatStates() {
  SEATS.forEach((p, i) => {
    const d = document.querySelector(`.seat[data-i="${i}"]`);
    if (!d) return;
    d.classList.toggle("here", !!p.here);
    const pfp = d.querySelector(".pfp");
    if (pfp) pfp.innerHTML = p.here ? "&#10003;" : "&#183;";
    if (p.here) {
      const r = d.querySelector(".poke");
      if (r) {
        const s = document.createElement("span");
        s.className = "st"; s.textContent = "已到庭";
        r.replaceWith(s);
      }
    }
  });
}

/* ---------- 宣判渲染：群众判决 + AI 判决分列 ---------- */
function renderVerdict(ev) {
  const tally = ev.tally || {};
  const guilty = ev.guilty_player_id;
  const counts = Object.values(tally).map(Number);
  const total = counts.reduce((a, b) => a + b, 0);
  const topCount = tally[String(guilty)] || 0;
  const majority = topCount > total / 2;

  const rest = Object.entries(tally)
    .filter(([pid]) => String(pid) !== String(guilty))
    .filter(([, v]) => v > 0)
    .map(([pid, v]) => `${v} 票 ${nameOfPlayer(Number(pid))}`)
    .join(" · ");

  const res = document.getElementById("voteResult");
  if (res) {
    res.textContent =
      (majority ? `${topCount} 票定罪` : `${topCount} 票领先 · 未过半`) +
      (rest ? ` · ${rest}` : "");
  }

  // 被告名字（宣判页主体）
  const nameEl = document.getElementById("guiltyName");
  if (nameEl && guilty != null) nameEl.textContent = nameOfPlayer(guilty);

  // AI 判决是否与群众一致 —— 产品核心看点
  const aiEl = document.getElementById("aiVerdict");
  if (aiEl) {
    if (ev.ai_verdict_player_id == null) {
      aiEl.style.display = "none";
    } else {
      aiEl.style.display = "";
      aiEl.textContent = ev.ai_agrees
        ? `本庭书记官附议：同判 ${nameOfPlayer(ev.ai_verdict_player_id)}。`
        : `本庭书记官持异议：其认定 ${nameOfPlayer(ev.ai_verdict_player_id)} 更该负责，但群众裁决为准。`;
    }
  }
}

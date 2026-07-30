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
  myVote: null,      // 我投给了谁（player_id），用于 UI 标记与重连回填
  voters: {},        // {voter_id: nominee_id}，重连后还原谁投了谁
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

/* ---------- 未登记身份的引导 ----------
   没有 player_id 的人无法 attend/vote（后端要内部 ID）。
   与其静默失败，不如明确告知并给出去登记的入口。 */
function showIdentityGate() {
  if (document.getElementById("idGate")) return;
  // /join 由后端提供。开发时前后端不同端口，同源部署时又是同一个域，
  // 所以从 API 基址推导，不写死相对路径（否则 4311 上会 404）。
  const joinURL = String(API).replace(/\/api\/?$/, "") + "/join";
  const gate = document.createElement("div");
  gate.id = "idGate";
  gate.innerHTML =
    '<div class="gatebox">' +
      '<div class="gatetitle">尚未登记身份</div>' +
      '<div class="gatetext">本庭需要核实你的 Steam 身份，' +
        '才能记录到庭与投票。登记一次即可，之后自动认人。</div>' +
      '<a class="btn gatebtn" href="' + joinURL + '">前往登记 · 报到</a>' +
      '<div class="gateskip">仅旁听，不参与表决</div>' +
    '</div>';
  gate.querySelector(".gateskip").onclick = () => gate.remove();
  document.body.appendChild(gate);
}
window.showIdentityGate = showIdentityGate;

/* ---------- 开庭 ---------- */
async function openTrial(matchId) {
  Trial.matchId = matchId;
  const me = await resolveMe();
  // 未登记的人进来只能旁听：先提示去 /join，不要等到点投票才静默失败
  if (me == null) showIdentityGate();
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
  // 先从投票明细还原"我投过谁"，否则重连后自己那票的标记会丢失
  Trial.voters = {};
  (t.votes || []).forEach(v => {
    Trial.voters[String(v.voter_id)] = v.nominee_id;
    if (Trial.me != null && v.voter_id === Trial.me) Trial.myVote = v.nominee_id;
  });
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
      if (ev.voter_id != null && ev.voter_id === Trial.me) {
        Trial.myVote = ev.nominee_id;
      }
      Trial.voters[String(ev.voter_id)] = ev.nominee_id;
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
  // 没身份就投不了：后端要内部 player_id，这里明确引导而不是静默失败
  if (Trial.me == null) { showIdentityGate(); return; }
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
    const mine = Trial.myVote != null && Trial.myVote === n.player_id;
    box.innerHTML = "";
    for (let k = 0; k < count; k++) {
      const a = document.createElement("div");
      // 我那一票排在最前并高亮标记，让人一眼看到自己投了谁
      const isMine = mine && k === 0;
      a.className = isMine ? "av mine" : "av";
      a.textContent = isMine ? "我" : "•";
      if (isMine) a.title = "你投的票";
      box.appendChild(a);
    }
  });
  // 已投票后锁定按钮，避免重复提交与"我到底投了没"的困惑
  const b = document.getElementById("voteBtn");
  if (b && Trial.myVote != null) {
    b.disabled = true;
    const who = nameOfPlayer(Trial.myVote);
    b.textContent = who ? "已投 " + who + " · 等待宣判" : "已投票 · 等待宣判";
  }
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

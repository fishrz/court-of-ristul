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
/* ---------- 身份：与 data-layer 的 CURRENT_PLAYER_ID 同一个解析结果 ----------
   曾经这里按 p.steam_id 匹配，但 GET /api/players 返回的 PlayerOption
   根本不含 steam_id —— 于是 Trial.me 永远是 null，attend/vote 全部静默失效。
   现在统一走 data-layer 的 loadPlayers()，只有一处解析逻辑。 */
async function resolveMe() {
  try {
    Trial.players = await api("/players");
  } catch (err) {
    return null;
  }
  let id = (typeof CURRENT_PLAYER_ID !== "undefined") ? CURRENT_PLAYER_ID : null;
  if (id == null && window.loadPlayers) {
    id = await loadPlayers();
  }
  Trial.me = id;
  // 没登记过：不假装是别人，返回 null，由调用方引导去 /join
  return id;
}
window.resolveMe = resolveMe;

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
  await attendLive();
  await syncTrialState(Trial.id);
  return Trial.id;
}

/* 我到庭了：告诉服务端，服务端广播给其他四个人。
   接口幂等，重复调用不会重复记录到场。 */
async function attendLive() {
  if (!Trial.id || Trial.simulated || Trial.me == null) return false;
  try {
    await api("/trials/" + Trial.id + "/attend", {
      method: "POST",
      body: JSON.stringify({ player_id: Trial.me })
    });
    return true;
  } catch (err) {
    console.warn("[法庭] 到庭登记失败：", err.message);
    return false;
  }
}
window.attendLive = attendLive;

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
  // 开庭门槛：数字全部由服务端下发（quorum / attendee_count / can_start）
  if (window.setQuorum) setQuorum(t);

  // 到场情况回填到候审室
  // 后端 attendances 是扁平的 player_id 数组，不是对象数组
  const here = new Set(t.attendances || []);
  SEATS.forEach((s) => {
    // 只认服务端到场名单。没有 player_id 的座位无法对号入座，一律视为未到，
    // 不能默认把第一个座位当成"我"点亮。
    s.here = s.player_id != null && here.has(s.player_id);
  });
  if (document.querySelector("#sW.on")) {
    renderSeatStates();
    updateWaitCount();
  }
  // 辩词已落库：刷新/重连后要还原，否则后进来的人看不到被告说了什么
  if (t.appeal_text) showAppealText(t.appeal_text);
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
        verdict: t.verdict || null,
        ai_verdict_player_id: t.ai_verdict_player_id,
        ai_agrees: t.verdict_player_id === t.ai_verdict_player_id
      });
      go(5);
    }
  }
  return t;
}
window.syncTrialState = syncTrialState;

/* ---------- 事件分发 ---------- */
function handleLiveEvent(ev) {
  if (!ev || !ev.type) return;
  switch (ev.type) {
    case "attend": {
      const i = SEATS.findIndex(s => s.player_id === ev.player_id);
      if (i >= 0) arrive(i);
      // 到庭人数变了，门槛提示与按钮态要跟着变。
      // attend 广播不带聚合数，按本地座位表重算 here，total/quorum 保持服务端值。
      if (window.QUORUM && window.setQuorum) {
        setQuorum({
          quorum: QUORUM.quorum,
          attendee_count: SEATS.filter(s => s.here).length,
          total: QUORUM.total,
          can_start: QUORUM.quorum != null &&
            SEATS.filter(s => s.here).length >= QUORUM.quorum
        });
      }
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
      showAppealText(ev.text, ev.player_id ? nameOfPlayer(ev.player_id) : null);
      break;
    case "verdict":
      Trial.status = "closed";
      renderVerdict(ev);
      go(5);
      break;
    // 书记官（DeepSeek）比开庭慢 ~15s，判词后到。此时多半还在举证/投票页，
    // 先把结果记下来；真正上屏由 renderVerdict / renderVerdictBody 负责。
    case "ai_opinion":
      Trial.aiOpinion = {
        player_id: ev.ai_verdict_player_id,
        reason: ev.reason,
        advice: ev.advice,
        overruled: ev.overruled,
      };
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

function showAppealText(text, who) {
  const sec = document.getElementById("appealSec");
  if (!sec || !text) return;
  const name = who || "被告";
  sec.innerHTML =
    '<div class="sec-h"><span class="t">最后陈述</span><span class="rule"></span>' +
    '<span class="n">已呈庭</span></div>' +
    '<div class="plea"><span class="qm">&ldquo;</span><p>' +
    String(text).replace(/</g, "&lt;") + "</p>" +
    '<div class="by">—— ' + name + " · 当庭陈述</div></div>";
}
window.showAppealText = showAppealText;

/* 把辩词呈给服务端，让所有人都看到（离线时退回本地渲染） */
async function submitAppealLive(text) {
  if (!Trial.id || Trial.simulated) return false;
  try {
    await api("/trials/" + Trial.id + "/appeal", {
      method: "POST",
      body: JSON.stringify({ text: text })
    });
    return true;   // 服务端会广播 appeal 事件，由 handleLiveEvent 渲染
  } catch (err) {
    return false;
  }
}
window.submitAppealLive = submitAppealLive;

/* ---------- 座位状态刷新（不重启模拟定时器，纯状态回填） ---------- */
function renderSeatStates() {
  SEATS.forEach((p, i) => {
    const d = document.querySelector(`.seat[data-i="${i}"]`);
    if (!d) return;
    d.classList.toggle("here", !!p.here);
    const pfp = d.querySelector(".pfp");
    if (pfp) pfp.innerHTML = p.here ? "&#10003;" : "&#183;";
    // 「你」的标注要在这里补：renderWait() 渲染时身份可能还没解析出来
    const sub = d.querySelector(".who i");
    if (sub && Trial.me != null && p.player_id === Trial.me) {
      const base = p.h || "";
      if (!sub.textContent.startsWith("你 ")) {
        sub.textContent = base ? "你 · " + base : "你";
      }
    }
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

  // 票面措辞同样走极性表：胜局是「票推举」，不是「票定罪」
  const cp = (typeof CP !== "undefined" && CP) ? CP : COPY.guilt;
  const decided = cp.polarity === "merit" ? "票推举" : "票定罪";
  const txt = document.getElementById("voteText") || document.getElementById("voteResult");
  if (txt) {
    txt.textContent =
      (majority ? `${topCount} ${decided}` : `${topCount} 票领先 · 未过半`) +
      (rest ? ` · ${rest}` : "");
  }

  // 出席率与副奖：都来自服务端。注意两条来源结构不同 ——
  // WS verdict 事件把 attendance/side_award 平铺在顶层，且 ev.verdict 是判词字符串；
  // syncTrialState 传来的 ev.verdict 才是 verdict_json 对象。
  const vj = (ev.verdict && typeof ev.verdict === "object") ? ev.verdict : ev;
  if (window.renderAttendance) renderAttendance(vj.attendance || ev.attendance);
  if (window.renderSideAward) renderSideAward(vj.side_award || ev.side_award);

  // 被告名字（宣判页主体）
  const nameEl = document.getElementById("guiltyName");
  if (nameEl && guilty != null) nameEl.textContent = nameOfPlayer(guilty);

  // 判词/英雄/建议正文按真实被告渲染（主脚本提供），否则 s5 会留着设计稿假判决
  // NOMINEES 是主脚本的 let 声明，不在 window 上，只能靠裸标识符取
  if (window.renderVerdictBody) {
    const list = (typeof NOMINEES !== "undefined" && NOMINEES) || [];
    const nom = guilty != null ? list.find(n => n.player_id === guilty) : null;
    // 无人得票 / 提名里找不到人时也要走一遍 —— 否则整页留着「—」占位，
    // 看起来像加载失败而不是「本庭没有推举」。
    window.renderVerdictBody(nom || null);
  }

  // AI 判决是否与群众一致 —— 产品核心看点
  const aiEl = document.getElementById("aiVerdict");
  if (aiEl) {
    if (ev.ai_verdict_player_id == null) {
      aiEl.style.display = "none";
    } else {
      aiEl.style.display = "";
      // 措辞跟着极性走：胜局书记官是在「推举」，不是在追责
      const merit = cp.polarity === "merit";
      const who = nameOfPlayer(ev.ai_verdict_player_id);
      if (ev.ai_agrees) {
        aiEl.textContent = merit
          ? `本庭书记官附议：同推 ${who}。`
          : `本庭书记官附议：同判 ${who}。`;
      } else {
        aiEl.textContent = merit
          ? `本庭书记官持异议：其认为 ${who} 更该受表彰，但群众表决为准。`
          : `本庭书记官持异议：其认定 ${who} 更该负责，但群众裁决为准。`;
      }
    }
  }
}

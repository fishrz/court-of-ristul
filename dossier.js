/* ============================================================
   本人卷宗 · 成长页
   ------------------------------------------------------------
   和法庭刻意分开的一层：法庭是公开处刑，这里是私人复盘。
   同一批事实，两种语气——把毒舌塞进这里会让建议显得不可信。

   数据全部来自后端 /api/dossier/*，没有任何前端造的数字。
   拿不到就显示「暂无」，绝不用假数据填充版面。
   ============================================================ */

const Dossier = {
  data: null,       // GET /api/dossier/{id}
  detail: null,     // GET /api/dossier/{id}/match/{mid}
  picked: null,     // 当前展开的 match_id
  coaching: false,  // 教练请求进行中
  coachCache: {},   // match_id -> 教练结果，避免重复烧 13 秒
};

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function fmtMin(sec) {
  if (sec == null) return '--:--';
  return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
}

/* ---------- 各区块渲染 ---------- */

function dsStats(d) {
  const r = d.record || {};
  const wr = r.winrate == null ? '--' : r.winrate + '%';
  return `<div class="stat3">
    <div class="cell"><div class="n">${r.matches || 0}</div><div class="l">收案</div></div>
    <div class="cell"><div class="n">${esc(wr)}</div><div class="l">胜率</div></div>
    <div class="cell"><div class="n ${r.convictions ? '' : 'dim'}">${r.convictions || 0}</div>
      <div class="l">案底</div></div>
  </div>`;
}

function dsHeroes(d) {
  const list = d.heroes || [];
  if (!list.length) return '';
  const max = Math.max(...list.map(h => h.matches));
  const rows = list.map(h => {
    // 样本不足时不显示胜率——1 场 100% 是噪音，摆出来就是骗人
    const wr = h.enough_sample
      ? `<span class="hw ${h.winrate >= 50 ? 'good' : 'bad'}">${h.winrate}%</span>`
      : `<span class="hw na">样本不足</span>`;
    return `<div class="hrow">
      <div style="flex:1">
        <div class="hn">${esc(h.hero_name)}</div>
        <div class="hbar"><i style="width:${Math.round(h.matches / max * 100)}%"></i></div>
      </div>
      <span class="hc">${h.matches} 场</span>${wr}
    </div>`;
  }).join('');
  return `<div class="secttl">英 雄 池<span class="hint">少于 3 场不计胜率</span></div>
    <div class="sec">${rows}</div>`;
}

function dsTrend(d) {
  if (!d.trend_available) {
    return `<div class="secttl">趋 势</div>
      <div class="idnote" style="margin-top:0">
        再打 ${(d.min_trend_matches || 5) - (d.record.matches || 0)} 局就能看出走势。
        场次太少时谈进步或退步都是错觉。
      </div>`;
  }
  if (!(d.trend || []).length) {
    return `<div class="secttl">趋 势</div>
      <div class="idnote" style="margin-top:0">最近这些局表现稳定，没有明显的变好或变坏。</div>`;
  }
  return `<div class="secttl">趋 势</div>` +
    d.trend.map(t => `<div class="fnd insight"><div class="ft">${esc(t)}</div></div>`).join('');
}

function dsMatches(d) {
  const list = d.recent || [];
  if (!list.length) return '';
  const cards = list.map(m => {
    const on = String(m.match_id) === String(Dossier.picked);
    const kda = `${m.kills}/${m.deaths}/${m.assists}`;
    return `<div class="mcard ${on ? 'on' : ''}" onclick="dsPick('${m.match_id}')">
      <div style="flex:1">
        <div class="mh">${esc(m.hero_name)}</div>
        <div class="mk">${kda} · ${fmtMin(m.duration)}</div>
      </div>
      <span class="mr ${m.we_won ? 'w' : 'l'}">${m.we_won ? '胜' : '负'}</span>
    </div>` + (on ? `<div id="dsDetail">${dsDetail()}</div>` : '');
  }).join('');
  return `<div class="secttl">最 近 比 赛<span class="hint">点开看诊断</span></div>
    <div class="mlist">${cards}</div>`;
}

function dsFindings(list) {
  if (!list.length) {
    return `<div class="fnd mitigating"><div class="ft">这局没查出明显问题。</div>
      <div class="fa">保持这个节奏。</div></div>`;
  }
  return list.map(f => `<div class="fnd ${esc(f.severity)}">
    <div class="ft">${esc(f.text)}</div>
    ${f.action ? `<div class="fa">${esc(f.action)}</div>` : ''}
  </div>`).join('');
}

function dsDetail() {
  const dt = Dossier.detail;
  if (dt === 'loading') return `<div class="idnote" style="margin:9px 0">正在调阅…</div>`;
  if (!dt) return '';
  if (!dt.parsed) {
    return `<div class="idnote" style="margin:9px 0">${esc(dt.note || '这局还没解析完')}</div>`;
  }
  const cached = Dossier.coachCache[dt.match_id];
  let coach;
  if (cached && cached.available) {
    // 字段名以后端 ai.coach() 的返回为准：root_cause / action / evidence /
    // why_not_others。曾经这里写的是 reasoning / advice，两个都不存在，
    // 结果框子渲染出来但内容全空——等了 13 秒看到个空壳。
    const ev = (cached.evidence || []).map(
      e => `<div class="cev">· ${esc(e)}</div>`).join('');
    coach = `<div class="coachbox"><div class="ch">教 练 复 盘</div>
      <div class="cb">${esc(cached.root_cause || '')}</div>
      ${ev ? `<div class="cevs">${ev}</div>` : ''}
      ${cached.why_not_others ? `<div class="cb cwhy">${esc(cached.why_not_others)}</div>` : ''}
      ${cached.action ? `<div class="cb cact">下一局：${esc(cached.action)}</div>` : ''}
    </div>`;
  } else if (cached) {
    // 降级要安静：LLM 不可用不是用户的错，也不该显示成故障
    coach = `<div class="idnote" style="margin:9px 16px">教练暂时不在，上面的诊断依然有效。</div>`;
  } else {
    coach = `<button class="coachbtn" onclick="dsCoach('${dt.match_id}')"
      ${Dossier.coaching ? 'disabled' : ''}>${Dossier.coaching ? '思 考 中 …' : '请 教 练 深 度 复 盘'}</button>`;
  }
  return dsFindings(dt.findings || []) + coach;
}

/* ---------- 交互 ---------- */

async function dsPick(mid) {
  if (String(Dossier.picked) === String(mid)) {   // 再点一次收起
    Dossier.picked = null; Dossier.detail = null; renderMe(); return;
  }
  Dossier.picked = mid; Dossier.detail = 'loading'; renderMe();
  try {
    Dossier.detail = await api(`/dossier/${Dossier.data.player.id}/match/${mid}`);
  } catch (e) {
    Dossier.detail = { parsed: false, note: '调阅失败，稍后再试' };
  }
  renderMe();
}

async function dsCoach(mid) {
  Dossier.coaching = true; renderMe();
  try {
    Dossier.coachCache[mid] = await api(
      `/dossier/${Dossier.data.player.id}/match/${mid}/coach`, { method: 'POST' });
  } catch (e) {
    Dossier.coachCache[mid] = { available: false };
  }
  Dossier.coaching = false; renderMe();
}

/* ---------- 主渲染：覆盖 index.html 里只显示身份的旧版 ---------- */

function dsIdCard(p) {
  return `<div class="idcard">
    ${p.avatar_url ? `<img class="ava" src="${esc(p.avatar_url)}" alt="">` : ''}
    <div class="nm">${esc(p.display_name)}</div>
    <div class="rl">Registered Defendant</div>
  </div>`;
}

function dsUnregistered(sid) {
  return `<div class="idcard"><div class="nm" style="color:var(--ember)">身份未登记</div>
    <div class="rl">Unidentified</div></div>
    <div class="idnote">本机还没有绑定 Steam 身份，无法到庭、举证或投票。${
      sid ? `<br>本机存有 Steam ID <span class="mono">${esc(sid)}</span>，但服务器上查无此人——可能是登记未完成。` : ''
    }</div>
    <div class="sec" style="margin-top:16px"><a href="/join"
      style="display:block;text-align:center;padding:14px;border:1px solid var(--gold);
      border-radius:var(--r);color:var(--gold);font-size:13px;letter-spacing:.2em">前 往 登 记</a></div>`;
}

function renderMe() {
  const el = document.getElementById('meBody');
  if (!el) return;
  const { sid, player } = meInfo();

  if (!player) { el.innerHTML = dsUnregistered(sid); return; }

  const d = Dossier.data;
  if (!d) {
    // 数据还没到：先把身份显示出来，别让页面空着
    el.innerHTML = dsIdCard(player) +
      `<div class="idnote">正在调阅卷宗…</div>`;
    dsLoad(player.id);
    return;
  }

  el.innerHTML =
    dsIdCard(d.player) +
    `<div class="idnote" style="margin-top:10px">${esc(d.bracket || '未知分段')} · 庭内编号 <span class="mono">#${d.player.id}</span></div>` +
    dsStats(d) +
    dsTrend(d) +
    dsMatches(d) +
    dsHeroes(d) +
    `<div class="idnote" style="margin-bottom:24px">
      比赛记录、判决与案底都存在服务器上，本机只记住「你是谁」。
      换手机或清除浏览器数据后重新登记同一个 Steam ID 即可找回。
    </div>`;
}

async function dsLoad(pid) {
  try {
    Dossier.data = await api(`/dossier/${pid}`);
  } catch (e) {
    console.warn('[卷宗] 加载失败', e);
    Dossier.data = null;
    const el = document.getElementById('meBody');
    if (el) el.innerHTML = el.innerHTML.replace('正在调阅卷宗…', '卷宗暂时调阅不到，稍后再试。');
    return;
  }
  renderMe();
}

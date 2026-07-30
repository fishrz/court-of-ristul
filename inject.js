#!/usr/bin/env node
/**
 * 把 memes.json + 引擎内联进 index.html，用真实数据生成 EVIDENCE / NOMINEES
 * 避免 file:// 下 fetch 跨域问题
 */
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const html = fs.readFileSync(path.join(HERE, 'index.html'), 'utf8');
const db   = JSON.parse(fs.readFileSync(path.join(HERE, 'memes.json'), 'utf8'));
const eng  = fs.readFileSync(path.join(HERE, 'memes-engine.js'), 'utf8');

// ── 真实数据（同 test-engine.js） ──
const TEAM = [
  { id:1, name:'WhatToSay', hero:'主宰', role:'carry', pos:'一号位',
    kills:6, deaths:6, assists:8, gpm:472, xpm:540, net_worth:16800,
    last_hits:212, hero_damage:18500, tower_damage:1240, hero_healing:0,
    lh_at_10:38, dn_at_10:6, xp_at_10:2900, tp_uses:7, buyback_count:0,
    obs_placed:2, sen_placed:1, camps_stacked:1, stuns:8.2, teamfight_participation:0.68 },
  { id:2, name:'黑刺', hero:'巨牙海民', role:'offlane', pos:'三号位',
    kills:2, deaths:9, assists:11, gpm:288, xpm:352, net_worth:9100,
    last_hits:64, hero_damage:7000, tower_damage:180, hero_healing:0,
    lh_at_10:1, dn_at_10:0, xp_at_10:1520, tp_uses:5, buyback_count:0,
    obs_placed:10, sen_placed:5, camps_stacked:3, stuns:22.5, teamfight_participation:0.71 },
  { id:3, name:'风希', hero:'莉娜', role:'mid', pos:'中单',
    kills:9, deaths:8, assists:9, gpm:498, xpm:612, net_worth:17200,
    last_hits:186, hero_damage:18600, tower_damage:675, hero_healing:0,
    lh_at_10:44, dn_at_10:8, xp_at_10:3400, tp_uses:13, buyback_count:1,
    obs_placed:1, sen_placed:0, camps_stacked:0, stuns:14.8, teamfight_participation:0.692 },
  { id:4, name:'奥妙', hero:'冰女', role:'support', pos:'五号位',
    kills:3, deaths:10, assists:14, gpm:265, xpm:310, net_worth:7400,
    last_hits:38, hero_damage:9200, tower_damage:95, hero_healing:1800,
    lh_at_10:6, dn_at_10:1, xp_at_10:1400, tp_uses:9, buyback_count:0,
    obs_placed:13, sen_placed:6, camps_stacked:5, stuns:31.2, teamfight_participation:0.74 },
  { id:5, name:'Perennis', hero:'莱恩', role:'support', pos:'四号位',
    kills:5, deaths:7, assists:5, gpm:243, xpm:288, net_worth:6800,
    last_hits:29, hero_damage:5400, tower_damage:60, hero_healing:0,
    lh_at_10:4, dn_at_10:0, xp_at_10:1250, tp_uses:8, buyback_count:0,
    obs_placed:3, sen_placed:1, camps_stacked:0, stuns:9.4, teamfight_participation:0.42 },
];
const tNW  = TEAM.reduce((s,p)=>s+p.net_worth,0);
const tDmg = TEAM.reduce((s,p)=>s+p.hero_damage,0);
TEAM.forEach(p => {
  p.gold_share = p.net_worth/tNW;
  p.damage_share = p.hero_damage/tDmg;
  p.kda_ratio = (p.kills+p.assists)/Math.max(p.deaths,1);
  p.duration = 2212;
});

const Memes = require(path.join(HERE,'memes-engine.js'));
const acc = Memes.accuse(db, TEAM, { mode:'private', contexts:['defeat'] });

// ── 生成 EVIDENCE：每人最多 1 条，保证罪证分散到不同被告 ──
const allEv = acc.suspects.flatMap(s =>
  s.evidence.filter(e => e.severity >= 2).map(e => ({ ...e, who:`${s.player.name} · ${s.player.hero}` })));
allEv.sort((a,b) => b.severity - a.severity);

const EVIDENCE = [];
const perPerson = {};
// 第一轮：每人取最重的一条
for (const e of allEv) {
  if (perPerson[e.who]) continue;
  perPerson[e.who] = 1;
  EVIDENCE.push({ tag:e.tag, who:e.who, fact:e.fact, quip:e.quip, id:e.id,
                  basis:e.basis.map(b=>`${b.label} ${b.value}`) });
  if (EVIDENCE.length >= 4) break;
}
// 第二轮：不足 4 条则允许每人第二条
if (EVIDENCE.length < 4) {
  for (const e of allEv) {
    if (EVIDENCE.some(x => x.id === e.id && x.who === e.who)) continue;
    if ((perPerson[e.who] || 0) >= 2) continue;
    perPerson[e.who] = (perPerson[e.who] || 0) + 1;
    EVIDENCE.push({ tag:e.tag, who:e.who, fact:e.fact, quip:e.quip, id:e.id,
                    basis:e.basis.map(b=>`${b.label} ${b.value}`) });
    if (EVIDENCE.length >= 4) break;
  }
}

// ── 生成 NOMINEES：定罪分前三 ──
const maxScore = Math.max(...acc.suspects.map(s=>s.score), 1);
const NOMINEES = acc.suspects.slice(0,3).map(s => {
  const p = s.player;
  const ev = s.evidence.filter(e => e.severity >= 1);
  // chips 去重：同一指标只出现一次
  const seenMetric = new Set();
  const chips = [];
  for (const e of ev) {
    for (const b of e.basis) {
      if (seenMetric.has(b.metric)) continue;
      seenMetric.add(b.metric);
      chips.push([`${b.label} ${b.value}`, e.severity >= 2 ? 1 : 0]);
    }
  }
  // 补正面数据作为平衡
  for (const e of s.evidence.filter(e => e.severity === 0 && e.tone.includes('praise'))) {
    for (const b of e.basis) {
      if (seenMetric.has(b.metric)) continue;
      seenMetric.add(b.metric);
      chips.push([`${b.label} ${b.value}`, 0]);
    }
  }
  return {
    nm: p.name, hr: `${p.hero} · ${p.pos}`,
    score: Math.round(s.score / maxScore * 100),
    charge: ev.length ? ev.map(e=>e.fact).filter(Boolean).join(' ') : '本局未发现明显失职。',
    chips: chips.slice(0,3)
  };
});

// ── 注入 ──
const payload =
`/* ══ 词库（memes.json v${db.version}，${db.entries.length} 条，语料 ${db.meta.corpus_size} 条）══ */
const MEMES_DB = ${JSON.stringify(db)};

${eng.replace(/^#!.*\n/, '')}

/* ── 本局真实数据（match 8917764448，已核实字段口径）── */
const TEAM_DATA = ${JSON.stringify(TEAM)};

/* ── 由词库引擎生成，非硬编码 ── */
const EVIDENCE = ${JSON.stringify(EVIDENCE, null, 2)};

const NOMINEES = ${JSON.stringify(NOMINEES, null, 2)};
`;

const startMark = '/* ── 真实数据（match 8917764448）驱动的罪证与提名 ── */';
const endMark   = '];\n\nconst NOMINEES';
const si = html.indexOf(startMark);
if (si < 0) { console.error('✗ 找不到注入锚点'); process.exit(1); }
// 找到 NOMINEES 数组结束
const ni = html.indexOf('const NOMINEES', si);
const ne = html.indexOf('\n];', ni) + 3;
if (ni < 0 || ne < 3) { console.error('✗ 找不到 NOMINEES 结束'); process.exit(1); }

const out = html.slice(0, si) + payload + html.slice(ne);
fs.writeFileSync(path.join(HERE,'index.html'), out, 'utf8');

console.log(`✓ 已注入 index.html`);
console.log(`  词库 ${db.entries.length} 条 / 引擎 ${(eng.length/1024).toFixed(1)}KB`);
console.log(`  EVIDENCE ${EVIDENCE.length} 条:`);
EVIDENCE.forEach(e => console.log(`    [${e.tag}] ${e.who} — ${e.quip}`));
console.log(`  NOMINEES ${NOMINEES.length} 人:`);
NOMINEES.forEach(n => console.log(`    ${n.nm} (${n.score}) chips=${JSON.stringify(n.chips)}`));
console.log(`  文件 ${(out.length/1024).toFixed(1)}KB`);

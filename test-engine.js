#!/usr/bin/env node
/**
 * 用 match 8917764448 真实数据验证词库引擎
 * 关键验证：Lina 不应被判有罪（她 TP 13 最勤、击杀第一）
 */
const fs = require('fs');
const path = require('path');
const Memes = require(path.join(__dirname, 'memes-engine.js'));
const db = JSON.parse(fs.readFileSync(path.join(__dirname, 'memes.json'), 'utf8'));

// ── match 8917764448 真实数据（已核实） ──
const TEAM = [
  { id:1, name:'WhatToSay', hero:'主宰',       role:'carry',
    kills:6, deaths:6, assists:8, gpm:472, xpm:540, net_worth:16800,
    last_hits:212, hero_damage:18500, tower_damage:1240, hero_healing:0,
    lh_at_10:38, dn_at_10:6, xp_at_10:2900, tp_uses:7, buyback_count:0,
    obs_placed:2, sen_placed:1, camps_stacked:1, stuns:8.2,
    teamfight_participation:0.68 },

  { id:2, name:'黑刺', hero:'巨牙海民',        role:'offlane',
    kills:2, deaths:9, assists:11, gpm:288, xpm:352, net_worth:9100,
    last_hits:64, hero_damage:7000, tower_damage:180, hero_healing:0,
    lh_at_10:1, dn_at_10:0, xp_at_10:1520, tp_uses:5, buyback_count:0,
    obs_placed:10, sen_placed:5, camps_stacked:3, stuns:22.5,
    teamfight_participation:0.71 },

  // ★ 关键：Lina。purchase_tpscroll=null 但 item_uses.tpscroll=13
  { id:3, name:'风希', hero:'莉娜',            role:'mid',
    kills:9, deaths:8, assists:9, gpm:498, xpm:612, net_worth:17200,
    last_hits:186, hero_damage:18600, tower_damage:675, hero_healing:0,
    lh_at_10:44, dn_at_10:8, xp_at_10:3400, tp_uses:13, buyback_count:1,
    obs_placed:1, sen_placed:0, camps_stacked:0, stuns:14.8,
    teamfight_participation:0.692 },

  { id:4, name:'奥妙', hero:'冰女',            role:'support',
    kills:3, deaths:10, assists:14, gpm:265, xpm:310, net_worth:7400,
    last_hits:38, hero_damage:9200, tower_damage:95, hero_healing:1800,
    lh_at_10:6, dn_at_10:1, xp_at_10:1400, tp_uses:9, buyback_count:0,
    obs_placed:13, sen_placed:6, camps_stacked:5, stuns:31.2,
    teamfight_participation:0.74 },

  { id:5, name:'Perennis', hero:'莱恩',        role:'support',
    kills:5, deaths:7, assists:5, gpm:243, xpm:288, net_worth:6800,
    last_hits:29, hero_damage:5400, tower_damage:60, hero_healing:0,
    lh_at_10:4, dn_at_10:0, xp_at_10:1250, tp_uses:8, buyback_count:0,
    obs_placed:3, sen_placed:1, camps_stacked:0, stuns:9.4,
    teamfight_participation:0.42 },
];

// 计算派生指标
const totalNW  = TEAM.reduce((s,p)=>s+p.net_worth,0);
const totalDmg = TEAM.reduce((s,p)=>s+p.hero_damage,0);
TEAM.forEach(p => {
  p.gold_share   = p.net_worth / totalNW;
  p.damage_share = p.hero_damage / totalDmg;
  p.kda_ratio    = (p.kills + p.assists) / Math.max(p.deaths, 1);
  p.duration     = 2212;   // 36:52
});

const line = (c='─') => console.log(c.repeat(64));

console.log('\n瑞斯图尔法庭 · 词库引擎验证');
console.log('Match 8917764448 · 36:52 · 败北\n');

// ══════════ 私密模式全队归因 ══════════
line('═');
console.log('私密五黑模式（severity ≤ 3）');
line('═');

const r = Memes.accuse(db, TEAM, { mode:'private', contexts:['defeat'] });

r.suspects.forEach((s, i) => {
  const p = s.player;
  console.log(`\n${i===0?'▶':' '} ${String(i+1).padStart(2)}. ${p.name} · ${p.hero} · ${p.role}`);
  console.log(`     ${p.kills}/${p.deaths}/${p.assists}  ` +
              `经济${(p.gold_share*100).toFixed(1)}%  ` +
              `伤害${(p.damage_share*100).toFixed(1)}%  ` +
              `TP${p.tp_uses}  参团${(p.teamfight_participation*100).toFixed(0)}%`);
  console.log(`     定罪分 ${s.score}`);
  if (!s.evidence.length) { console.log('     └ 无有效证据'); return; }
  s.evidence.forEach(e => {
    console.log(`     ├ [${e.severity}] ${e.tag} — ${e.fact || ''}`);
    console.log(`     │   「${e.quip}」`);
    console.log(`     │   依据: ${e.basis.map(b=>`${b.label}=${b.value}`).join(', ')}`);
  });
});

// ══════════ 硬断言 ══════════
console.log('\n');
line('═');
console.log('回归断言');
line('═');

const byName = n => r.suspects.find(s => s.player.name === n);
const lina   = byName('风希');
const hei    = byName('黑刺');
const per    = byName('Perennis');

const checks = [];
const chk = (name, pass, detail='') => { checks.push({name,pass,detail}); };

// ★ 最关键：Lina 不能因 TP 被【指控】（tp_diligent 是表扬，不算）
const linaTPBad = lina.evidence.find(e => e.category === 'tp_rotation' && e.severity >= 1);
chk('Lina 未因 TP 被指控', !linaTPBad,
    linaTPBad ? `误判！命中 ${linaTPBad.id}` : `TP=13 队内最高，正确豁免`);

// 且应该拿到正面的「支援勤快」
const linaTPGood = lina.evidence.find(e => e.id === 'tp_diligent');
chk('Lina 获得「支援勤快」肯定', !!linaTPGood,
    linaTPGood ? linaTPGood.quip : '未命中');

// Lina 不该是头号嫌疑人
chk('Lina 非头号嫌疑人', r.suspects[0].player.name !== '风希',
    `实际头号: ${r.suspects[0].player.name}`);

// 黑刺应因劣单崩盘被判（lh_at_10=1，role=offlane）
const heiLane = hei.evidence.find(e => e.category === 'lane');
chk('黑刺因劣单崩盘被判', !!heiLane,
    heiLane ? `${heiLane.tag} (lh@10=1)` : '未命中');

// 黑刺应命中插眼冠军（10眼但伤害11.9%）
const heiVision = hei.evidence.find(e => e.id === 'vision_ward_only');
chk('黑刺命中「插眼冠军」', !!heiVision,
    heiVision ? heiVision.quip : '未命中');

// 场景文案不得混入玩家证据
const polluted = r.suspects.flatMap(s => s.evidence)
  .filter(e => ['court','loading','share'].includes(e.category));
chk('场景文案未混入归因', polluted.length === 0,
    polluted.length ? `泄漏: ${polluted.map(e=>e.id).join(',')}` : 'court/loading/share 已隔离');

// 辅助不该因经验低被判（辅助经验本就低）
const supXp = r.suspects
  .filter(s => s.player.role === 'support')
  .flatMap(s => s.evidence)
  .filter(e => e.id === 'lane_xp_starved');
chk('辅助未因经验低被误判', supXp.length === 0,
    supXp.length ? '误判！' : 'role 白名单生效');

// Perennis 参团 42% 应被判团战绝缘
const perTF = per.evidence.find(e => e.category === 'teamfight');
chk('Perennis 因参团低被判', !!perTF,
    perTF ? `${perTF.tag} (参团42%)` : '未命中');

// null 安全：造一个未解析的玩家
const unparsed = { ...TEAM[0], id:99, name:'未解析',
  lh_at_10:null, teamfight_participation:null, obs_placed:null, tp_uses:null };
const unEv = Memes.select(db, unparsed, TEAM, { mode:'private', contexts:['data_incomplete'] });
const leaked = unEv.filter(e =>
  e.basis.some(b => ['lh_at_10','teamfight_participation','obs_placed','tp_uses'].includes(b.metric)));
chk('null 指标不产生指控', leaked.length === 0,
    leaked.length ? `泄漏: ${leaked.map(e=>e.id).join(',')}` : 'requires 闸门生效');

// 公开模式必须降级
const pub = Memes.select(db, byName('Perennis').player, TEAM, { mode:'public', contexts:['defeat'] });
const harsh = pub.filter(e => e.severity >= 2);
chk('公开模式无重损词条', harsh.length === 0,
    harsh.length ? `泄漏: ${harsh.map(e=>e.id).join(',')}` : `仅 ${pub.length} 条轻损`);

// 变量注入不留残缺
const allText = r.suspects.flatMap(s => s.evidence)
  .flatMap(e => [e.tag, e.fact, e.quip, e.verdict, e.share]).filter(Boolean);
const unresolved = allText.filter(t => /\{[a-z_]+\}/.test(t));
chk('无未注入变量', unresolved.length === 0,
    unresolved.length ? unresolved[0] : `${allText.length} 条文案全部注入`);

// 每条判词都有依据
const noBasis = r.suspects.flatMap(s=>s.evidence).filter(e => e.severity>=2 && !e.basis.length);
chk('重损判词均有数据依据', noBasis.length === 0,
    noBasis.length ? noBasis.map(e=>e.id).join(',') : '全部可解释');

console.log('');
checks.forEach(c => console.log(`  ${c.pass?'✓':'✗'} ${c.name}${c.detail?'  — '+c.detail:''}`));

const failed = checks.filter(c=>!c.pass).length;
console.log('');
line('═');
console.log(failed ? `✗ ${failed} 项失败` : `✓ ${checks.length} 项断言全部通过`);
line('═');

// ══════════ 公开模式对照 ══════════
console.log('\n公开分享模式（severity ≤ 1）对照：\n');
r.suspects.slice(0,3).forEach(s => {
  const ev = Memes.select(db, s.player, TEAM, { mode:'public', contexts:['defeat'] });
  console.log(`  ${s.player.name}: ${ev.length ? ev.map(e=>`[${e.tag}] ${e.quip}`).join(' / ') : '（无可公开指控）'}`);
});

console.log('');
process.exit(failed ? 1 : 0);

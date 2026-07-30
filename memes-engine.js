/**
 * 瑞斯图尔法庭 —— 词库选择引擎
 *
 * 职责：把「已验证数据」映射到「文案」，严格单向。
 *   数据 → requires闸门 → 上下文过滤 → severity上限 → trigger匹配 → 排序去重 → 变体
 *
 * 绝不允许反向：先造梗再倒找数据。
 */

(function (global) {
  'use strict';

  const MODE_LIMITS = {
    private: 3,   // 熟人五黑，可以放开损
    public:  1,   // 公开分享，只允许轻损
    safe:    0    // 纯中性，任何攻击性都不出
  };

  // 这些分类是「场景文案」，不是对玩家的指控，绝不参与归因
  const NON_ACCUSATORY = new Set(['court', 'loading', 'share']);

  /** 队内排名：返回 0-based 名次，null 值排在最后 */
  function rankOf(players, metric, pid) {
    const vals = players
      .filter(p => p[metric] != null)
      .sort((a, b) => b[metric] - a[metric]);
    const idx = vals.findIndex(p => p.id === pid);
    return idx < 0 ? null : idx;
  }

  /** 单条件求值 */
  function evalCond(cond, ctx) {
    const { metric, op, value } = cond;

    // role / position 是上下文属性，不是 OpenDota 指标
    if (metric === 'role' || metric === 'position' || metric === 'is_core') {
      const actual = ctx.player[metric];
      if (op === 'in')     return value.includes(actual);
      if (op === 'not_in') return !value.includes(actual);
      if (op === '==')     return actual === value;
      if (op === '!=')     return actual !== value;
      return false;
    }

    // rank_is 比较队内排名
    if (op === 'rank_is') {
      const r = rankOf(ctx.team, metric, ctx.player.id);
      if (r == null) return false;
      const n = ctx.team.filter(p => p[metric] != null).length;
      if (value === 'first') return r === 0;
      if (value === 'last')  return r === n - 1;
      return r === value;
    }

    const actual = ctx.player[metric];
    // null 绝不当 0 —— 这是 Lina 事件的核心教训
    if (actual == null) return false;

    switch (op) {
      case '<':  return actual <  value;
      case '<=': return actual <= value;
      case '>':  return actual >  value;
      case '>=': return actual >= value;
      case '==': return actual === value;
      case '!=': return actual !== value;
      case 'in':     return value.includes(actual);
      case 'not_in': return !value.includes(actual);
      default:   return false;
    }
  }

  /** trigger 求值：all 全真，any 任一真 */
  function evalTrigger(trig, ctx) {
    if (!trig) return true;
    if (trig.all && !trig.all.every(c => evalCond(c, ctx))) return false;
    if (trig.any && !trig.any.some(c => evalCond(c, ctx))) return false;
    return true;
  }

  /** 格式化指标值 */
  function fmt(metricName, val, metrics) {
    if (val == null) return '—';
    const m = metrics[metricName];
    if (!m) return String(val);
    if (m.type === 'pct')   return (val * 100).toFixed(1) + '%';
    if (m.type === 'float') return val.toFixed(1);
    if (metricName === 'duration') {
      const s = Math.round(val);
      return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }
    return String(val);
  }

  /** 变量注入 */
  function inject(str, ctx, metrics) {
    if (!str) return str;
    return str.replace(/\{(\w+)\}/g, (whole, key) => {
      if (key === 'player') return ctx.player.name ?? whole;
      if (key === 'hero')   return ctx.player.hero ?? whole;
      if (key === 'n')      return ctx.caseNo ?? whole;
      if (key === 'date')   return ctx.date ?? whole;
      if (key === 'baseline' || key === 'team_avg') {
        return ctx.baseline?.[key] ?? whole;
      }
      const v = ctx.player[key];
      return v == null ? whole : fmt(key, v, metrics);
    });
  }

  /** 简单字符串 hash，用于稳定选变体 */
  function hash(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) {
      h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    }
    return Math.abs(h);
  }

  /**
   * 主入口
   * @param {object} db      memes.json
   * @param {object} player  单个玩家的指标对象 {id,name,hero,role,gpm,deaths,...}
   * @param {array}  team    全队玩家数组（算排名用）
   * @param {object} opts    { mode:'private'|'public'|'safe', contexts:[], seed, max }
   */
  function select(db, player, team, opts) {
    opts = opts || {};
    const mode     = opts.mode || 'private';
    const contexts = new Set(opts.contexts || []);
    const limit    = MODE_LIMITS[mode] ?? 3;
    const max      = opts.max ?? 4;
    const metrics  = db.metrics;

    const ctx = {
      player, team, metrics,
      caseNo: opts.caseNo, date: opts.date,
      baseline: opts.baseline || {}
    };

    const hits = db.entries.filter(e => {
      // 0. 场景文案不参与玩家归因
      if (NON_ACCUSATORY.has(e.category)) return false;

      // 0b. 无 trigger 的兜底词条（如「证据不足」）只能被显式请求
      if (!e.trigger && !opts.includeFallback) return false;

      // 1. severity 上限
      if (e.severity > limit) return false;

      // 2. requires 闸门 —— 所有必需指标必须存在且非 null
      for (const r of (e.requires || [])) {
        if (player[r] == null) return false;
      }

      // 3. 禁用上下文
      for (const f of (e.forbidden_context || [])) {
        if (contexts.has(f)) return false;
      }

      // 4. trigger
      return evalTrigger(e.trigger, ctx);
    });

    // 5. 排序：severity 降序
    hits.sort((a, b) => b.severity - a.severity);

    // 6. 同 category 去重
    const seen = new Set();
    const picked = [];
    for (const e of hits) {
      if (seen.has(e.category)) continue;
      seen.add(e.category);
      picked.push(e);
      if (picked.length >= max) break;
    }

    // 7. 变体选择 + 变量注入
    const seed = opts.seed ?? (player.id ?? 0);
    return picked.map(e => {
      const pool = [e.text, ...(e.variants || []).map(v => ({ ...e.text, ...v }))];
      const t = pool[hash(String(seed) + e.id) % pool.length];

      // public/safe 模式下，quip 降级为 safe 文案
      const useSafe = mode !== 'private' && e.severity >= 1;

      return {
        id: e.id,
        category: e.category,
        severity: e.severity,
        tone: e.tone,
        tag:     inject(t.tag, ctx, metrics),
        fact:    inject(t.fact, ctx, metrics),
        quip:    useSafe ? inject(t.safe, ctx, metrics) : inject(t.quip, ctx, metrics),
        verdict: inject(t.verdict, ctx, metrics),
        share:   inject(t.share, ctx, metrics),
        safe:    inject(t.safe, ctx, metrics),
        // 可解释性：这条判词依据了哪些数据
        basis: (e.requires || []).map(r => ({
          metric: r,
          label: metrics[r]?.label ?? r,
          value: fmt(r, player[r], metrics),
          source: metrics[r]?.source
        }))
      };
    });
  }

  /** 取单条通用文案（court / loading / share / neutral） */
  function pick(db, category, opts) {
    opts = opts || {};
    const pool = db.entries.filter(e => e.category === category);
    if (!pool.length) return null;
    const e = pool[(opts.index ?? hash(String(opts.seed ?? Date.now()))) % pool.length];
    const ctx = { player: {}, team: [], metrics: db.metrics,
                  caseNo: opts.caseNo, date: opts.date, baseline: {} };
    return {
      id: e.id,
      tag:   inject(e.text.tag, ctx, db.metrics),
      quip:  inject(e.text.quip, ctx, db.metrics),
      share: inject(e.text.share, ctx, db.metrics)
    };
  }

  /**
   * 全队归因：给每人算证据，返回排序后的嫌疑人
   * 定罪分 = Σ(severity²)，避免单一可疑字段主导
   */
  function accuse(db, team, opts) {
    opts = opts || {};
    const results = team.map(p => {
      const ev = select(db, p, team, opts);
      const score = ev.reduce((s, e) => s + e.severity * e.severity, 0);
      return { player: p, evidence: ev, score };
    });
    results.sort((a, b) => b.score - a.score);

    // 全队都没有实质证据 → 无罪释放
    const anyGuilt = results.some(r => r.score >= 4);
    return { suspects: results, noGuilty: !anyGuilt };
  }

  const API = { select, pick, accuse, MODE_LIMITS, _rankOf: rankOf };

  if (typeof module !== 'undefined' && module.exports) module.exports = API;
  else global.Memes = API;

})(typeof window !== 'undefined' ? window : globalThis);

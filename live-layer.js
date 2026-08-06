/* ============================================================
   瑞斯图尔法庭 · 实时层（WebSocket）
   ------------------------------------------------------------
   部署兼容性是这一层的首要约束。目标形态是香港/新加坡的
   HTTPS 域名 + 反向代理（Caddy/Nginx），前端与后端同源。
   因此这里遵守四条硬规则：

   1. 绝不硬编码 ws:// 或 host。scheme 从 location.protocol 推导，
      HTTPS 页面必须用 wss://，否则浏览器直接拦截混合内容。
   2. WS 地址从 API 基址推导，保证「前端在哪、后端就在哪」，
      同源部署时自动变成 wss://<域名>/ws/...，无需改代码。
   3. 反向代理和移动网络会静默掐掉空闲连接（Nginx 默认 60s），
      所以必须有应用层心跳，不能依赖 TCP keepalive。
   4. 微信内切后台、地铁断网、代理重启都会断线，
      必须自动重连 + 重连后用 HTTP 拉一次权威状态补齐丢失事件。
      WS 只做「推送提速」，HTTP 才是唯一事实来源。
   ============================================================ */

const LIVE_CFG = {
  heartbeatMs: 25000,   // < 反代常见 60s 空闲超时
  maxBackoffMs: 15000,
  baseBackoffMs: 800
};

function wsURL(trialId) {
  // 从 API 基址推导，同源部署时自动跟随域名与协议
  const base = new URL(API, location.href);
  const scheme = base.protocol === "https:" ? "wss:" : "ws:";
  // API 形如 https://court.example.com/api -> 去掉 /api 得到根
  const root = base.pathname.replace(/\/api\/?$/, "");
  return scheme + "//" + base.host + root + "/ws/trials/" + trialId;
}

const Live = {
  sock: null,
  trialId: null,
  hbTimer: null,
  retryTimer: null,
  attempts: 0,
  closedByUs: false,

  connect(trialId) {
    this.disconnect();
    this.trialId = trialId;
    this.closedByUs = false;
    this._open();
  },

  _open() {
    const url = wsURL(this.trialId);
    let sock;
    try {
      sock = new WebSocket(url);
    } catch (err) {
      console.warn("[法庭] WS 构造失败，退回轮询：", err.message);
      return this._scheduleRetry();
    }
    this.sock = sock;

    sock.onopen = () => {
      this.attempts = 0;
      console.info("[法庭] 实时连接已建立", url);
      this._startHeartbeat();
      // 重连后可能错过了事件，用 HTTP 拉一次权威状态补齐
      syncTrialState(this.trialId);
      setLiveBadge(true);
    };

    sock.onmessage = (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch (e) { return; }
      if (data && data.type === "pong") return;
      handleLiveEvent(data);
    };

    sock.onclose = () => {
      this._stopHeartbeat();
      setLiveBadge(false);
      if (!this.closedByUs) this._scheduleRetry();
    };

    sock.onerror = () => { /* onclose 会接手重连 */ };
  },

  _scheduleRetry() {
    if (this.closedByUs) return;
    this.attempts += 1;
    // 指数退避 + 抖动，避免五个人同时重连打爆后端
    const wait = Math.min(
      LIVE_CFG.maxBackoffMs,
      LIVE_CFG.baseBackoffMs * Math.pow(2, this.attempts - 1)
    ) * (0.7 + Math.random() * 0.6);
    clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => this._open(), wait);
  },

  _startHeartbeat() {
    this._stopHeartbeat();
    this.hbTimer = setInterval(() => {
      if (this.sock && this.sock.readyState === WebSocket.OPEN) {
        // 后端 receive_text 只是维持连接，收到什么都行
        try { this.sock.send("ping"); } catch (e) {}
      }
    }, LIVE_CFG.heartbeatMs);
  },

  _stopHeartbeat() {
    clearInterval(this.hbTimer);
    this.hbTimer = null;
  },

  disconnect() {
    this.closedByUs = true;
    clearTimeout(this.retryTimer);
    this._stopHeartbeat();
    if (this.sock) {
      try { this.sock.close(); } catch (e) {}
      this.sock = null;
    }
    setLiveBadge(false);
  }
};

/* 微信/Safari 切后台会冻结连接，回前台立刻校正状态 */
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (!Live.trialId || Live.closedByUs) return;
  if (!Live.sock || Live.sock.readyState !== WebSocket.OPEN) {
    Live.attempts = 0;
    Live._open();
  } else {
    syncTrialState(Live.trialId);
  }
});

/* Pick the Play — helpers shared by the player app and the admin console. */
"use strict";

const PTP = (() => {
  // ------------------------------------------------------------ server clock
  // The server stamps every snapshot with `server_time`; timers count down
  // against server time so every phone shows the same seconds remaining.
  let clockOffset = 0;
  const syncClock = (serverTime) => {
    if (typeof serverTime === "number") clockOffset = serverTime - Date.now() / 1000;
  };
  const now = () => Date.now() / 1000 + clockOffset;

  // ------------------------------------------------------------ DOM helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /** Build an element. Strings become text nodes, so user content is never parsed as HTML. */
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key === "style") Object.assign(node.style, value);
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function toast(message, kind = "info", ms = 3200) {
    let host = $(".toasts");
    if (!host) {
      host = el("div", { class: "toasts", role: "status", "aria-live": "polite" });
      document.body.append(host);
    }
    const t = el("div", { class: `toast ${kind}` }, message);
    host.append(t);
    setTimeout(() => {
      t.classList.add("out");
      setTimeout(() => t.remove(), 320);
    }, ms);
  }

  // ------------------------------------------------------------ HTTP
  async function api(path, { method = "GET", body, token, adminKey } = {}) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = `Bearer ${token}`;
    if (adminKey) headers["X-Admin-Key"] = adminKey;
    const res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
      const err = new Error(detail || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ------------------------------------------------------------ WebSocket
  /**
   * Auto-reconnecting socket. `hello()` returns the first message sent on each
   * (re)connect; `onMessage` receives parsed JSON; `onStatus` gets
   * "connecting" | "online" | "offline".
   */
  class LiveSocket {
    constructor(path, { hello, onMessage, onStatus, onClose } = {}) {
      this.path = path;
      this.hello = hello;
      this.onMessage = onMessage || (() => {});
      this.onStatus = onStatus || (() => {});
      this.onClose = onClose || (() => {});
      this.retry = 0;
      this.stopped = false;
      this.ws = null;
      this.pingTimer = null;
      document.addEventListener("visibilitychange", () => {
        // iOS suspends sockets in the background; reconnect as soon as we're back.
        if (document.visibilityState === "visible" && !this.isOpen()) this.connect(true);
      });
    }

    url() {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      return `${proto}//${location.host}${this.path}`;
    }

    isOpen() { return this.ws && this.ws.readyState === WebSocket.OPEN; }

    connect(immediate = false) {
      if (this.stopped) return;
      clearTimeout(this.reconnectTimer);
      if (this.ws && this.ws.readyState <= WebSocket.OPEN) {
        if (!immediate) return;
        this.ws.onclose = null;
        this.ws.close();
      }
      this.onStatus("connecting");
      const ws = new WebSocket(this.url());
      this.ws = ws;
      ws.onopen = () => {
        this.retry = 0;
        if (this.hello) ws.send(JSON.stringify(this.hello()));
        this.onStatus("online");
        clearInterval(this.pingTimer);
        this.pingTimer = setInterval(() => this.send({ type: "ping" }), 25000);
      };
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg && typeof msg.server_time === "number") syncClock(msg.server_time);
        this.onMessage(msg);
      };
      ws.onclose = (ev) => {
        clearInterval(this.pingTimer);
        this.onStatus("offline");
        if (this.onClose(ev) === false) { this.stopped = true; return; }
        this.scheduleReconnect();
      };
      ws.onerror = () => { /* onclose follows */ };
    }

    scheduleReconnect() {
      if (this.stopped) return;
      const delay = Math.min(10000, 500 * 2 ** this.retry) + Math.random() * 300;
      this.retry += 1;
      this.reconnectTimer = setTimeout(() => this.connect(), delay);
    }

    send(obj) {
      if (!this.isOpen()) return false;
      this.ws.send(JSON.stringify(obj));
      return true;
    }

    stop() {
      this.stopped = true;
      clearInterval(this.pingTimer);
      clearTimeout(this.reconnectTimer);
      if (this.ws) this.ws.close();
    }
  }

  // ------------------------------------------------------------ football helpers
  const ordinal = (n) => ({ 1: "1st", 2: "2nd", 3: "3rd", 4: "4th" })[n] || `${n}th`;

  function downDistance(play) {
    if (!play || !play.down) return "";
    const dist = play.distance ? String(play.distance) : "";
    return dist ? `${ordinal(play.down)} & ${dist}` : ordinal(play.down);
  }

  // Distance buckets by total yards gained on the play (a loss never scores distance points).
  const YARDAGE_RANGE = { SHORT: "0-5 yds", MEDIUM: "6-10 yds", LONG: "11+ yds", LOSS: "Loss of yards" };

  /** SHORT 0-5 (incomplete / no gain = 0), MEDIUM 6-10, LONG 11+, LOSS below 0; null if not a number. */
  function bucketForYards(yards) {
    if (yards === null || yards === undefined || yards === "" || !Number.isInteger(Number(yards))) return null;
    const y = Number(yards);
    if (y < 0) return "LOSS";
    if (y <= 5) return "SHORT";
    if (y <= 10) return "MEDIUM";
    return "LONG";
  }

  const yardsText = (yards) => `${yards} ${Math.abs(yards) === 1 ? "yd" : "yds"}`;
  const titleCase = (word) => (word ? word.charAt(0) + word.slice(1).toLowerCase() : "");

  /** "Run · Left · Medium (7 yds)" for a resolved play (title case, as players read it). */
  function describeResult(play) {
    if (!play || !play.correct_play_type) return "";
    const parts = [titleCase(play.correct_play_type), titleCase(play.correct_direction)];
    if (play.correct_yardage) {
      parts.push(titleCase(play.correct_yardage) +
        (play.yards_gained === null || play.yards_gained === undefined ? "" : ` (${yardsText(play.yards_gained)})`));
    }
    return parts.join(" · ");
  }

  function teamAbbr(name) {
    const words = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return "—";
    if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
    return words.map((w) => w[0]).join("").slice(0, 3).toUpperCase();
  }

  /** Black or white text, whichever reads better on the given background. */
  function inkFor(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return "#ffffff";
    const n = parseInt(m[1], 16);
    const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    const L = 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
    return L > 0.4 ? "#0a0f18" : "#ffffff";
  }

  function applyTeamColors(game, root = document.documentElement) {
    if (!game) return;
    const set = (k, v) => root.style.setProperty(k, v);
    set("--home-1", game.home_primary);
    set("--home-2", game.home_secondary);
    set("--home-ink", inkFor(game.home_primary));
    set("--away-1", game.away_primary);
    set("--away-2", game.away_secondary);
    set("--away-ink", inkFor(game.away_primary));
  }

  /** A score to show: a whole number from 0 up. Null (no score yet) or anything odd gives null, so nothing is drawn. */
  function scoreText(value) {
    return typeof value === "number" && Number.isInteger(value) && value >= 0 && value < 1000 ? String(value) : null;
  }

  /**
   * The live score beside each team (and, in the host console, how old it is). The server fills it in only when a
   * check of the live data happens, so it can trail the TV by about a minute. Both scores must be good to show
   * either one; with none yet (or the practice game) both stay hidden.
   */
  function renderScore(root, game) {
    const away = scoreText(game.away_score);
    const home = scoreText(game.home_score);
    const show = away !== null && home !== null;
    for (const [side, text] of [["away", away], ["home", home]]) {
      const node = $(`.team.${side} .team-score`, root);
      if (!node) continue;
      node.hidden = !show;
      const want = show ? text : "";
      if (node.textContent !== want) node.textContent = want;
    }
    root.dataset.scoreAt = show && typeof game.score_at === "number" && Number.isFinite(game.score_at) ? String(game.score_at) : "";
    paintScoreAge(root);
  }

  /** "Score as of 12 s ago" in the host console's scorebug (the player pages have no such line). */
  function paintScoreAge(root) {
    const node = root && $(".bug-age", root);
    if (!node) return;
    const at = root.dataset.scoreAt ? Number(root.dataset.scoreAt) : NaN;
    node.hidden = !Number.isFinite(at);
    if (node.hidden) return;
    const age = Math.max(0, Math.round(now() - at));
    const text = `Score as of ${age < 90 ? `${age} s` : `${Math.round(age / 60)} min`} ago`;
    if (node.textContent !== text) node.textContent = text;
  }

  /** Fill a scorebug element (see templates) from a game + play. */
  function renderScorebug(root, game, play) {
    if (!root) return;
    root.hidden = !game;
    if (!game) return;
    applyTeamColors(game);
    $(".team.away .team-abbr", root).textContent = teamAbbr(game.away_name);
    $(".team.away .team-name", root).textContent = game.away_name;
    $(".team.home .team-abbr", root).textContent = teamAbbr(game.home_name);
    $(".team.home .team-name", root).textContent = game.home_name;
    renderScore(root, game);
    const pill = $(".status-pill", root);
    pill.textContent = game.status;
    pill.className = `status-pill ${String(game.status).toLowerCase()}`;
    const info = [];
    if (play) info.push(`Play ${play.play_number}`);
    const dd = downDistance(play);
    if (dd) info.push(dd);
    $(".bug-info", root).textContent = info.join(" · ") || "Pre-game";
  }

  const pct = (part, total) => (total ? Math.round((100 * part) / total) : 0);

  /** Horizontal percentage bars for pick splits; updates in place so widths animate. */
  function crowdBars(container, stats, keys) {
    const total = (stats && stats.total) || 0;
    if (container.dataset.keys !== keys.join()) {
      container.dataset.keys = keys.join();
      container.replaceChildren(...keys.map((key) =>
        el("div", { class: "bar-row", dataset: { key } },
          el("span", {}, key),
          el("div", { class: "bar" }, el("span")),
          el("span", { class: "pct" }, "0%"))));
    }
    requestAnimationFrame(() => {
      for (const row of container.children) {
        const share = pct((stats && stats[row.dataset.key]) || 0, total);
        row.querySelector(".bar > span").style.width = `${share}%`;
        row.querySelector(".pct").textContent = `${share}%`;
      }
    });
  }

  // ------------------------------------------------------------ light / dark theme
  // base.html applies the saved choice before first paint; this wires the toggle buttons
  // (every page header has one), saves the choice and keeps the browser chrome color in step.
  const theme = (() => {
    const KEY = "ptp-theme";
    const BAR = { dark: "#070b12", light: "#f2f5fa" };
    const root = document.documentElement;
    const get = () => (root.getAttribute("data-theme") === "light" ? "light" : "dark");

    function paint() {
      const current = get();
      const label = current === "light" ? "Switch to dark mode" : "Switch to light mode";
      for (const btn of $$("[data-theme-toggle]")) {
        btn.setAttribute("aria-label", label);
        btn.setAttribute("title", label);
      }
      const bar = $('meta[name="theme-color"]');
      if (bar) bar.setAttribute("content", BAR[current]);
      const scheme = $('meta[name="color-scheme"]');
      if (scheme) scheme.setAttribute("content", current);
    }

    function set(next, save = true) {
      root.setAttribute("data-theme", next === "light" ? "light" : "dark");
      if (save) {
        try { localStorage.setItem(KEY, get()); } catch { /* private mode: applies for this visit only */ }
      }
      paint();
    }

    document.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-theme-toggle]")) set(get() === "light" ? "dark" : "light");
    });
    // Another tab changed the choice: follow it.
    window.addEventListener("storage", (ev) => {
      if (ev.key === KEY && (ev.newValue === "light" || ev.newValue === "dark")) set(ev.newValue, false);
    });
    paint();
    return { get, set };
  })();

  return {
    $, $$, el, toast, api, LiveSocket, now, syncClock, theme,
    ordinal, downDistance, teamAbbr, inkFor, applyTeamColors, renderScorebug, paintScoreAge, pct, crowdBars,
    YARDAGE_RANGE, bucketForYards, yardsText, titleCase, describeResult,
  };
})();

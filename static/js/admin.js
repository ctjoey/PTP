/* Pick the Play — admin console. Drives the play state machine over /ws/admin. */
"use strict";

(() => {
  const { $, $$, el, toast, LiveSocket, now, downDistance } = PTP;

  const KEY_STORAGE = "ptp_admin_key";
  const ACK_TIMEOUT_MS = 8000;

  const storage = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
    del: (k) => { try { localStorage.removeItem(k); } catch { /* private mode */ } },
  };

  const A = {
    state: null,
    down: 1,
    rtype: null,
    rdir: null,
    busy: false,
    seq: 0,
    pending: new Map(),
    timer: null,
  };
  let socket = null;

  // ------------------------------------------------------------------ auth

  function showAuth(message = "") {
    if (socket) socket.stop();
    socket = null;
    $("#console").hidden = true;
    $("#auth").hidden = false;
    $("#auth-error").textContent = message;
    $("#admin-key").focus();
  }

  function start(key) {
    if (socket) socket.stop();
    socket = new LiveSocket("/ws/admin", {
      hello: () => ({ type: "auth", key }),
      onMessage: handleMessage,
      onStatus: setConn,
      onClose: (ev) => (ev.code === 4401 ? false : undefined),
    });
    socket.connect();
  }

  function setConn(status) {
    const dot = $("#conn");
    dot.className = `conn ${status}`;
    dot.setAttribute("aria-label", status);
  }

  // ------------------------------------------------------------------ messaging

  function handleMessage(msg) {
    switch (msg.type) {
      case "auth_error":
        storage.del(KEY_STORAGE);
        showAuth(msg.message);
        break;
      case "admin_state":
        $("#auth").hidden = true;
        $("#console").hidden = false;
        A.state = msg;
        render();
        break;
      case "admin_ack": {
        const done = A.pending.get(msg.request_id);
        A.pending.delete(msg.request_id);
        if (done) done(msg);
        if (!msg.ok) toast(msg.error, "error", 4500);
        break;
      }
      default:
        break;
    }
  }

  /** Send an admin action and resolve with its ack (or null on timeout/offline). */
  function act(action, payload = {}) {
    if (!socket || !socket.isOpen()) {
      toast("Not connected to the server.", "error");
      return Promise.resolve(null);
    }
    const requestId = ++A.seq;
    A.busy = true;
    render();
    return new Promise((resolve) => {
      const finish = (ack) => {
        clearTimeout(timeout);
        A.pending.delete(requestId);
        A.busy = A.pending.size > 0;
        render();
        resolve(ack);
      };
      const timeout = setTimeout(() => {
        toast("The server didn't answer in time.", "error");
        finish(null);
      }, ACK_TIMEOUT_MS);
      A.pending.set(requestId, finish);
      socket.send({ action, request_id: requestId, ...payload });
    });
  }

  // ------------------------------------------------------------------ render

  function render() {
    const st = A.state;
    if (!st) return;
    const { game, play } = st;
    const active = play && play.state !== "RESOLVED";

    PTP.renderScorebug($("#scorebug"), game, play);
    $("#online-chip").textContent = `${st.players_online} players · ${st.spectators_online} watching`;
    $("#game-meta").textContent = game
      ? `Game #${game.id}: ${game.away_name} @ ${game.home_name} — ${game.status}`
      : "No game yet. Create one above.";

    $("#go-live").disabled = A.busy || !game || game.status !== "SCHEDULED";
    $("#go-final").disabled = A.busy || !game || game.status === "FINAL" || active;
    $("#create-game-btn").disabled = A.busy || active;

    // Play state panel
    $("#ctrl-play").textContent = play
      ? [`Play ${play.play_number}`, downDistance(play)].filter(Boolean).join(" · ")
      : "No play yet";
    const badge = $("#ctrl-badge");
    badge.textContent = play ? (play.voided ? "VOIDED" : play.state) : "IDLE";
    badge.className = `state-badge ${play && !play.voided ? play.state : ""}`;
    syncTimer();

    // Controller buttons
    $("#open-btn").disabled = A.busy || !game || game.status === "FINAL" || active;
    $("#lock-btn").disabled = A.busy || !play || play.state !== "OPEN";
    for (const b of $$("[data-rtype]")) {
      b.classList.toggle("selected", b.dataset.rtype === A.rtype);
      b.disabled = !active;
    }
    for (const b of $$("[data-rdir]")) {
      b.classList.toggle("selected", b.dataset.rdir === A.rdir);
      b.disabled = !active;
    }
    $("#resolve-btn").disabled = A.busy || !play || play.state !== "LOCKED" || !A.rtype || !A.rdir;
    $("#void-btn").disabled = A.busy || !active;
    for (const b of $$("[data-down]")) b.classList.toggle("selected", Number(b.dataset.down) === A.down);

    // Live pick stats
    const stats = st.pick_stats || {};
    $("#kpi-picks").textContent = String(stats.total || 0);
    $("#kpi-online").textContent = String(st.players_online);
    $("#kpi-ranked").textContent = String(st.ranked_players);
    PTP.crowdBars($("#admin-crowd"), stats, ["RUN", "PASS", "LEFT", "CENTER", "RIGHT"]);

    renderBoard(st.leaderboard);
    renderHistory(st.history);
  }

  function syncTimer() {
    clearInterval(A.timer);
    const node = $("#ctrl-timer");
    const play = A.state && A.state.play;
    if (!play || play.state !== "OPEN") {
      node.textContent = play && play.state === "LOCKED" ? "🔒" : "--";
      node.style.color = "";
      return;
    }
    const tick = () => {
      const rem = Math.max(0, play.locks_at - now());
      node.textContent = `${rem.toFixed(1)}s`;
      node.style.color = rem <= 3 ? "var(--danger)" : rem <= 7 ? "var(--warn)" : "var(--accent)";
      if (rem <= 0) clearInterval(A.timer);
    };
    tick();
    A.timer = setInterval(tick, 100);
  }

  function renderBoard(rows) {
    const board = $("#board");
    if (!rows.length) {
      board.replaceChildren(el("li", { class: "empty" }, "No scores yet."));
      return;
    }
    board.replaceChildren(...rows.map((r) =>
      el("li", { class: "board-row" },
        el("span", { class: "board-rank" }, String(r.rank)),
        el("span", { class: "board-name" },
          el("strong", {}, r.username),
          el("span", {}, `${r.picks} picks · ${r.exact_hits} exact`)),
        el("span", { class: "board-score" }, String(r.score)))));
  }

  function renderHistory(rows) {
    const list = $("#history");
    if (!rows.length) {
      list.replaceChildren(el("li", { class: "empty", style: { display: "block" } }, "Plays you run show up here."));
      return;
    }
    list.replaceChildren(...rows.map((p) => {
      const result = p.voided ? "VOID"
        : p.state === "RESOLVED" ? `${p.correct_play_type} · ${p.correct_direction}`
          : p.state;
      return el("li", {},
        el("span", { class: "h-num" }, `#${p.play_number}`),
        el("span", {},
          el("span", { class: "h-res" }, result),
          downDistance(p) ? el("span", { class: "muted" }, ` · ${downDistance(p)}`) : null),
        el("span", { class: "h-meta" }, `${p.picks} picks · ${p.exact_hits} exact`));
    }));
  }

  // ------------------------------------------------------------------ actions

  async function createGame(ev) {
    ev.preventDefault();
    const form = new FormData($("#game-form"));
    const payload = Object.fromEntries(form.entries());
    const game = A.state && A.state.game;
    if (game && game.status !== "FINAL" &&
        !confirm(`Start a new game? "${game.away_name} @ ${game.home_name}" will be marked FINAL.`)) return;
    const ack = await act("create_game", payload);
    if (ack && ack.ok) {
      A.rtype = A.rdir = null;
      A.down = 1;
      $("#distance").value = "10";
      render();
      toast("Game created. Open the first play when you're ready.", "success");
    }
  }

  async function openPlay() {
    const windowSeconds = Number($("#window").value) || 15;
    const ack = await act("open_play", {
      down: A.down,
      distance: $("#distance").value.trim() || null,
      window_seconds: Math.min(60, Math.max(5, windowSeconds)),
    });
    if (ack && ack.ok) {
      A.rtype = A.rdir = null;
      render();
    }
  }

  async function resolvePlay() {
    const ack = await act("resolve_play", { play_type: A.rtype, direction: A.rdir });
    if (ack && ack.ok) {
      toast(`Scored: ${A.rtype} · ${A.rdir}`, "success");
      A.rtype = A.rdir = null;
      render();
    }
  }

  const click = (id) => {
    const btn = $(id);
    if (btn && !btn.disabled) btn.click();
  };

  function wireUI() {
    $("#auth-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const key = $("#admin-key").value;
      if (!key) return;
      storage.set(KEY_STORAGE, key);
      $("#auth-error").textContent = "";
      start(key);
    });
    $("#logout").addEventListener("click", () => {
      storage.del(KEY_STORAGE);
      showAuth();
    });

    $("#game-form").addEventListener("submit", createGame);
    $("#go-live").addEventListener("click", () => act("set_status", { status: "LIVE" }));
    $("#go-final").addEventListener("click", () => {
      if (confirm("End the game and mark it FINAL?")) act("set_status", { status: "FINAL" });
    });

    for (const b of $$("[data-down]")) {
      b.addEventListener("click", () => {
        const d = Number(b.dataset.down);
        A.down = A.down === d ? null : d;
        render();
      });
    }
    $("#open-btn").addEventListener("click", openPlay);
    $("#lock-btn").addEventListener("click", () => act("lock_play"));
    for (const b of $$("[data-rtype]")) b.addEventListener("click", () => { A.rtype = b.dataset.rtype; render(); });
    for (const b of $$("[data-rdir]")) b.addEventListener("click", () => { A.rdir = b.dataset.rdir; render(); });
    $("#resolve-btn").addEventListener("click", resolvePlay);
    $("#void-btn").addEventListener("click", () => {
      if (confirm("Void this play? Nobody scores and players see 'No play'.")) act("void_play");
    });

    document.addEventListener("keydown", (ev) => {
      if ($("#console").hidden || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (ev.target.closest("input, textarea, select")) return;
      const map = {
        o: () => click("#open-btn"),
        l: () => click("#lock-btn"),
        r: () => click('[data-rtype="RUN"]'),
        p: () => click('[data-rtype="PASS"]'),
        arrowleft: () => click('[data-rdir="LEFT"]'),
        arrowup: () => click('[data-rdir="CENTER"]'),
        arrowright: () => click('[data-rdir="RIGHT"]'),
        enter: () => click("#resolve-btn"),
      };
      const fn = map[ev.key.toLowerCase()];
      if (fn) {
        ev.preventDefault();
        fn();
      }
    });
  }

  wireUI();
  const saved = storage.get(KEY_STORAGE);
  if (saved) start(saved);
  else showAuth();
})();

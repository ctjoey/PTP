/* Pick the Play — admin console. Drives the play state machine over /ws/admin. */
"use strict";

(() => {
  const { $, $$, el, toast, LiveSocket, now, downDistance, bucketForYards, yardsText } = PTP;

  const KEY_STORAGE = "ptp_admin_key";
  const ACK_TIMEOUT_MS = 8000;

  const storage = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
    del: (k) => { try { localStorage.removeItem(k); } catch { /* private mode */ } },
  };

  const A = {
    key: null,
    state: null,
    down: 1,
    rtype: null,
    rdir: null,
    ryard: null,
    playId: null,
    busy: false,
    seq: 0,
    pending: new Map(),
    timer: null,
  };
  let socket = null;

  // Live data (Tank01 feed) lives in admin-feed.js; it only needs these few hooks into the console.
  const feed = PTPFeed.create({
    act: (action, payload) => act(action, payload),
    getState: () => A.state,
    getKey: () => A.key,
    isBusy: () => A.busy,
    loadResult,
    applyNextDown,
    refreshHistory: () => renderHistory((A.state && A.state.history) || []),
  });

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
    A.key = key;
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
      case "admin_state": {
        $("#auth").hidden = true;
        $("#console").hidden = false;
        A.state = msg;
        const playId = msg.play ? msg.play.id : null;
        if (playId !== A.playId) {
          // A new play (perhaps opened from another console): start its result entry fresh.
          A.playId = playId;
          clearResult();
        }
        render();
        break;
      }
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
    for (const b of $$("[data-ryard]")) {
      b.classList.toggle("selected", b.dataset.ryard === A.ryard);
      b.disabled = !active;
    }
    $("#yards").disabled = !active;
    $("#resolve-btn").disabled = A.busy || !resolveReady();
    $("#void-btn").disabled = A.busy || !active;
    for (const b of $$("[data-down]")) b.classList.toggle("selected", Number(b.dataset.down) === A.down);

    // Live pick stats
    const stats = st.pick_stats || {};
    $("#kpi-picks").textContent = String(stats.total || 0);
    $("#kpi-online").textContent = String(st.players_online);
    $("#kpi-ranked").textContent = String(st.ranked_players);
    PTP.crowdBars($("#admin-crowd"), stats, ["RUN", "PASS", "LEFT", "MIDDLE", "RIGHT", "SHORT", "MEDIUM", "LONG"]);

    renderBoard(st.leaderboard);
    renderHistory(st.history);
    feed.render(st);
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
          el("span", {}, `${count(r.picks, "pick")} · ${r.exact_hits} perfect`)),
        el("span", { class: "board-score" }, String(r.score)))));
  }

  /** Rebuilt only when something shown changed, so an open Fix editor keeps its focus while picks stream in. */
  function renderHistory(rows) {
    const list = $("#history");
    const sig = JSON.stringify([rows, feed.fixSig()]);
    if (sig === A.historySig) return;
    A.historySig = sig;
    if (!rows.length) {
      list.replaceChildren(el("li", { class: "empty", style: { display: "block" } }, "Plays you run show up here."));
      feed.afterHistory();
      return;
    }
    list.replaceChildren(...rows.map((p) => {
      const result = p.voided ? "VOID"
        : p.state === "RESOLVED" ? resultText(p.correct_play_type, p.correct_direction, p.correct_yardage, p.yards_gained)
          : p.state;
      const extras = feed.rowExtras(p);
      const li = el("li", {},
        el("span", { class: "h-num" }, `#${p.play_number}`),
        el("span", {},
          el("span", { class: "h-res" }, result),
          downDistance(p) ? el("span", { class: "muted" }, ` · ${downDistance(p)}`) : null,
          ...extras.tags.map((t) => [" ", t])),
        el("span", { class: "h-meta" }, `${count(p.picks, "pick")} · ${p.exact_hits} perfect`, extras.action));
      feed.mountEditor(li, p);
      return li;
    }));
    feed.afterHistory();
  }

  const count = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  /** "PASS · LEFT · MEDIUM (7 yds)"; older plays may have no distance. */
  function resultText(type, dir, yardage, yards) {
    const parts = [type, dir];
    if (yardage) parts.push(yards === null || yards === undefined ? yardage : `${yardage} (${yardsText(yards)})`);
    return parts.join(" · ");
  }

  // ------------------------------------------------------------------ actions

  async function createGame(ev) {
    ev.preventDefault();
    const form = new FormData($("#game-form"));
    const payload = Object.fromEntries(form.entries());
    if (!payload.feed_game_id) delete payload.feed_game_id;
    const game = A.state && A.state.game;
    if (game && game.status !== "FINAL" &&
        !confirm(`Start a new game? "${game.away_name} @ ${game.home_name}" will be marked FINAL.`)) return;
    const ack = await act("create_game", payload);
    if (ack && ack.ok) {
      clearResult();
      A.down = 1;
      $("#distance").value = "10";
      const followed = feed.afterCreate();
      render();
      toast(followed === "live" ? "Game created and following the live feed. Open the first play when you're ready."
        : followed === "demo" ? "Practice game created. Open the first play when you're ready."
          : "Game created. Open the first play when you're ready.", "success");
    }
  }

  function clearResult() {
    A.rtype = A.rdir = A.ryard = null;
    $("#yards").value = "";
  }

  /** Put a result (a feed suggestion, say) into the manual controls so Resolve & Score applies it. */
  function loadResult(parts) {
    if (parts.play_type) A.rtype = parts.play_type;
    if (parts.direction) A.rdir = parts.direction;
    if (parts.yardage) A.ryard = parts.yardage;
    if (parts.yards !== null && parts.yards !== undefined) $("#yards").value = String(parts.yards);
    render();
    const missing = !A.rtype ? '[data-rtype="RUN"]' : !A.rdir ? '[data-rdir="LEFT"]' : !A.ryard ? '[data-ryard="SHORT"]' : "#resolve-btn";
    const target = $(missing);
    if (target && !target.disabled) target.focus();
    $(".result-grid").scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }

  /** Fill Down and To go for the next play (from the feed) unless the host already did. */
  function applyNextDown(nd) {
    const down = Number(nd.down);
    if (down >= 1 && down <= 4) A.down = down;
    if (nd.distance !== null && nd.distance !== undefined) $("#distance").value = String(nd.distance);
    for (const b of $$("[data-down]")) b.classList.toggle("selected", Number(b.dataset.down) === A.down);
  }

  /** The yards field as a whole number, null when empty, NaN when not a valid number. */
  function typedYards() {
    const raw = $("#yards").value.trim();
    if (raw === "") return null;
    const n = Number(raw);
    return Number.isInteger(n) && n >= -99 && n <= 99 ? n : NaN;
  }

  function resolveReady() {
    const play = A.state && A.state.play;
    return !!(play && play.state === "LOCKED" && A.rtype && A.rdir && A.ryard && !Number.isNaN(typedYards()));
  }

  /** Pick a distance bucket; a typed yardage that disagrees with it is cleared. */
  function chooseYardage(bucket) {
    A.ryard = bucket;
    const yards = typedYards();
    if (yards !== null && bucketForYards(yards) !== bucket) $("#yards").value = "";
    render();
  }

  function presetsFromPage() {
    try {
      return JSON.parse($("#team-presets").textContent) || [];
    } catch {
      return [];
    }
  }

  /** Team preset dropdowns fill the name and both colors; editing them by hand switches to Custom. */
  function wirePresets() {
    const presets = presetsFromPage();
    const form = $("#game-form");
    for (const select of $$("[data-preset]")) {
      const side = select.dataset.preset;
      const field = (k) => form.elements[`${side}_${k}`];
      select.addEventListener("change", () => {
        const team = presets[Number(select.value)];
        if (select.value === "" || !team) {
          field("name").focus();
          field("name").select();
          return;
        }
        field("name").value = team.name;
        field("primary").value = team.primary.toLowerCase();
        field("secondary").value = team.secondary.toLowerCase();
      });
      const syncSelect = () => {
        const name = field("name").value.trim().toLowerCase();
        const match = presets.findIndex((t) => t.name.toLowerCase() === name &&
          t.primary.toLowerCase() === field("primary").value.toLowerCase() &&
          t.secondary.toLowerCase() === field("secondary").value.toLowerCase());
        select.value = match >= 0 ? String(match) : "";
      };
      for (const k of ["name", "primary", "secondary"]) field(k).addEventListener("input", syncSelect);
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
      clearResult();
      render();
    }
  }

  async function resolvePlay() {
    if (!resolveReady()) return;
    const yards = typedYards();
    const payload = { play_type: A.rtype, direction: A.rdir, yardage: A.ryard };
    if (yards !== null) payload.yards = yards;
    const ack = await act("resolve_play", payload);
    if (ack && ack.ok) {
      const p = ack.result;
      toast(`Scored: ${resultText(p.correct_play_type, p.correct_direction, p.correct_yardage, p.yards_gained)}`, "success");
      clearResult();
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
    wirePresets();
    $("#go-live").addEventListener("click", () => act("set_status", { status: "LIVE" }));
    $("#go-final").addEventListener("click", () => {
      if (confirm("End the game and mark it FINAL?")) act("set_status", { status: "FINAL" });
    });

    $("#distance").addEventListener("input", () => feed.ddEdited());
    for (const b of $$("[data-down]")) {
      b.addEventListener("click", () => {
        feed.ddEdited();
        const d = Number(b.dataset.down);
        A.down = A.down === d ? null : d;
        render();
      });
    }
    $("#open-btn").addEventListener("click", openPlay);
    $("#lock-btn").addEventListener("click", () => act("lock_play"));
    // Choosing a result by hand stops the feed suggestion's timer so the two can't race.
    for (const b of $$("[data-rtype]")) b.addEventListener("click", () => { feed.manualEdit(); A.rtype = b.dataset.rtype; render(); });
    for (const b of $$("[data-rdir]")) b.addEventListener("click", () => { feed.manualEdit(); A.rdir = b.dataset.rdir; render(); });
    for (const b of $$("[data-ryard]")) b.addEventListener("click", () => { feed.manualEdit(); chooseYardage(b.dataset.ryard); });
    const yardsInput = $("#yards");
    yardsInput.addEventListener("input", () => {
      feed.manualEdit();
      const yards = typedYards();
      if (yards !== null && !Number.isNaN(yards)) A.ryard = bucketForYards(yards);
      render();
    });
    yardsInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        click("#resolve-btn");
      } else if (ev.key === "Escape") {
        yardsInput.blur();
      }
    });
    $("#resolve-btn").addEventListener("click", resolvePlay);
    $("#void-btn").addEventListener("click", () => {
      if (confirm("Void this play? Nobody scores and players see 'No play'.")) act("void_play");
    });

    document.addEventListener("keydown", (ev) => {
      if ($("#console").hidden || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (ev.target.closest("input, textarea, select, #fix-editor, summary")) return;
      // Enter on a focused Live data button (Score now, Pause...) presses that button, as keyboard users expect.
      if (ev.key === "Enter" && ev.target.closest("#feed-panel")) return;
      if (feed.handleKey(ev)) {
        ev.preventDefault();
        return;
      }
      const map = {
        o: () => click("#open-btn"),
        l: () => click("#lock-btn"),
        r: () => click('[data-rtype="RUN"]'),
        p: () => click('[data-rtype="PASS"]'),
        arrowleft: () => click('[data-rdir="LEFT"]'),
        arrowup: () => click('[data-rdir="MIDDLE"]'),
        arrowright: () => click('[data-rdir="RIGHT"]'),
        s: () => click('[data-ryard="SHORT"]'),
        m: () => click('[data-ryard="MEDIUM"]'),
        g: () => click('[data-ryard="LONG"]'),
        x: () => click('[data-ryard="LOSS"]'),
        y: () => {
          if (!yardsInput.disabled) {
            yardsInput.focus();
            yardsInput.select();
          }
        },
        enter: () => {
          const resolve = $("#resolve-btn");
          if (!resolve.disabled) resolve.click();
          else feed.scoreFromKey();
        },
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

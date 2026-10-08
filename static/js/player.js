/* Pick the Play — live player app (also powers /lounge/<code>). */
"use strict";

(() => {
  const { $, $$, el, toast, api, LiveSocket, now, downDistance, pct, yardsText } = PTP;

  const TOKEN_KEY = "ptp_token";
  const loungeId = document.body.dataset.lounge || null;

  const storage = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
    del: (k) => { try { localStorage.removeItem(k); } catch { /* private mode */ } },
  };

  const S = {
    token: storage.get(TOKEN_KEY),
    user: null,
    state: null,
    view: "loading",
    board: loungeId ? "lounge" : "global",
    selection: { playId: null, type: null, dir: null, yard: null },
    savedKey: null,
    saving: false,
    timer: null,
    timerPlayId: null,
    animatedPlayId: null,
    ranks: { global: new Map(), lounge: new Map() },
    moves: { global: new Map(), lounge: new Map() },
    gameId: null,
  };
  let socket = null;

  // ------------------------------------------------------------------ boot

  async function init() {
    wireUI();
    showRememberedNotice();
    const params = new URLSearchParams(location.search);
    if (params.has("missing_lounge")) {
      const code = params.get("missing_lounge");
      toast(code ? `No lounge found with code ${code}.` : "That lounge doesn't exist.", "error");
      history.replaceState(null, "", "/");
    }

    if (S.token) {
      try {
        S.user = (await api("/api/me", { token: S.token })).user;
      } catch (err) {
        if (err.status === 401) forgetToken();
      }
    }

    // Spectate right away; reconnect with credentials once signed in.
    socket = new LiveSocket("/ws", {
      hello: () => ({ type: "hello", token: S.token, lounge: loungeId }),
      onMessage: handleMessage,
      onStatus: setConn,
    });
    socket.connect();

    let reconnect = false;
    if (!S.token) {
      await signIn();
      reconnect = true;
    }
    if (loungeId) reconnect = (await ensureLoungeMember()) || reconnect;
    if (reconnect) socket.connect(true);
  }

  function forgetToken() {
    S.token = null;
    S.user = null;
    storage.del(TOKEN_KEY);
  }

  function signIn() {
    const modal = $("#signin-modal");
    const input = $("#username");
    const error = $("#signin-error");
    const submit = $("#signin-submit");
    modal.hidden = false;
    setTimeout(() => input.focus(), 50);
    return new Promise((resolve) => {
      $("#signin-form").onsubmit = async (ev) => {
        ev.preventDefault();
        error.textContent = "";
        const username = input.value.trim();
        if (username.length < 2) {
          error.textContent = "Pick a username with at least 2 characters.";
          return;
        }
        submit.disabled = true;
        try {
          const user = await api("/api/users", { method: "POST", body: { username } });
          S.token = user.token;
          S.user = user;
          storage.set(TOKEN_KEY, user.token);
          modal.hidden = true;
          toast(`You're in, ${user.username}! 🏈`, "success");
          resolve();
        } catch (err) {
          error.textContent = err.message;
        } finally {
          submit.disabled = false;
        }
      };
    });
  }

  async function ensureLoungeMember() {
    try {
      await api(`/api/lounges/${encodeURIComponent(loungeId)}/join`, { method: "POST", token: S.token });
      return true;
    } catch (err) {
      if (err.status === 404) {
        location.replace(`/?missing_lounge=${encodeURIComponent(loungeId)}`);
      } else {
        toast(err.message, "error");
      }
      return false;
    }
  }

  // ------------------------------------------------------------------ socket

  function setConn(status) {
    const dot = $("#conn");
    dot.className = `conn ${status}`;
    dot.setAttribute("aria-label", { online: "Live", connecting: "Connecting", offline: "Offline" }[status]);
    if (status === "offline" && S.state) setPickStatus("Reconnecting…", "error");
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "state":
        applyState(msg);
        break;
      case "prediction_saved":
        onPredictionSaved(msg.prediction);
        break;
      case "announcement":
        showAnnouncement(msg);
        break;
      case "error":
        if (msg.code === "bad_token" || msg.code === "account_deleted") {
          // Removed by the host: say why once the page comes back to the sign-in.
          if (msg.code === "account_deleted" && msg.message && msg.message !== "Your account was deleted.") rememberNotice(msg.message);
          forgetToken();
          location.reload();
          return;
        }
        S.saving = false;
        toast(msg.message, "error");
        if (S.view === "open") paintPicks(msg.message);
        break;
      default:
        break;
    }
  }

  // ------------------------------------------------------------------ messages from the host

  const DISMISSED_KEY = "ptp_announce_dismissed";
  const NOTICE_KEY = "ptp_notice";

  /** The host's banner. Empty text hides it; a player's ✕ hides that one message until the host sends another. */
  function showAnnouncement(msg) {
    const box = $("#announce");
    const text = String(msg.text || "");
    let dismissed = null;
    try { dismissed = sessionStorage.getItem(DISMISSED_KEY); } catch { /* private mode */ }
    if (!text || String(msg.id) === dismissed) {
      box.hidden = true;
      return;
    }
    $("#announce-text").textContent = text;
    box.dataset.id = String(msg.id);
    box.hidden = false;
  }

  function dismissAnnouncement() {
    const box = $("#announce");
    box.hidden = true;
    try { sessionStorage.setItem(DISMISSED_KEY, box.dataset.id || ""); } catch { /* private mode */ }
  }

  function rememberNotice(text) {
    try { sessionStorage.setItem(NOTICE_KEY, text); } catch { /* private mode */ }
  }

  function showRememberedNotice() {
    let text = null;
    try {
      text = sessionStorage.getItem(NOTICE_KEY);
      sessionStorage.removeItem(NOTICE_KEY);
    } catch { /* private mode */ }
    if (text) toast(text, "error", 8000);
  }

  // ------------------------------------------------------------------ state

  function applyState(st) {
    const prev = S.state;
    S.state = st;
    if (st.me) S.user = { ...(S.user || {}), ...st.me };

    const gameId = st.game ? st.game.id : null;
    if (gameId !== S.gameId) {
      S.gameId = gameId;
      S.ranks = { global: new Map(), lounge: new Map() };
      S.moves = { global: new Map(), lounge: new Map() };
    }
    trackRankMoves(st, st.event === "play_resolved");

    PTP.renderScorebug($("#scorebug"), st.game, st.play);
    renderLoungeStrip(st.lounge);
    renderStage(st);
    renderMe(st, prev);
    renderBoard();
  }

  function trackRankMoves(st, recordMoves) {
    const boards = { global: st.leaderboard || [], lounge: (st.lounge && st.lounge.leaderboard) || [] };
    for (const [name, rows] of Object.entries(boards)) {
      const before = S.ranks[name];
      const after = new Map(rows.map((r) => [r.user_id, r.rank]));
      if (recordMoves) {
        S.moves[name] = new Map(
          rows.filter((r) => before.has(r.user_id)).map((r) => [r.user_id, before.get(r.user_id) - r.rank]),
        );
      }
      if (recordMoves || before.size === 0) S.ranks[name] = after;
    }
  }

  function pickView(st) {
    const { game, play } = st;
    if (!game) return "nogame";
    if (game.status === "FINAL" && (!play || play.state === "RESOLVED")) return "final";
    if (!play) return "waiting";
    return { OPEN: "open", LOCKED: "locked", RESOLVED: "result" }[play.state] || "waiting";
  }

  function showView(name) {
    if (S.view === name) return;
    S.view = name;
    for (const v of $$("[data-view]")) v.hidden = v.dataset.view !== name;
    if (name !== "open") stopTimer();
  }

  function playLabel(play) {
    return [`Play ${play.play_number}`, downDistance(play)].filter(Boolean).join(" · ");
  }

  function renderStage(st) {
    const view = pickView(st);
    showView(view);
    if (view === "waiting") renderWaiting(st);
    else if (view === "open") renderOpen(st);
    else if (view === "locked") renderLocked(st);
    else if (view === "result") renderResult(st);
    else if (view === "final") renderFinal(st);
  }

  function renderWaiting(st) {
    const scheduled = st.game.status === "SCHEDULED";
    $("#waiting-kicker").textContent = scheduled ? "Kickoff soon" : "Get ready";
    $("#waiting-title").textContent = scheduled ? "The game hasn't started yet" : "Waiting for the next play…";
  }

  // -- OPEN: the pick grid + countdown -------------------------------------

  function remaining() {
    const play = S.state && S.state.play;
    return play ? Math.max(0, play.locks_at - now()) : 0;
  }

  const pickKey = (type, dir, yard) => (type && dir && yard ? `${type}|${dir}|${yard}` : null);

  function renderOpen(st) {
    const play = st.play;
    if (S.selection.playId !== play.id) {
      const mine = st.my_prediction;
      S.selection = mine
        ? { playId: play.id, type: mine.play_type, dir: mine.direction, yard: mine.yardage || null }
        : { playId: play.id, type: null, dir: null, yard: null };
      S.savedKey = mine ? pickKey(mine.play_type, mine.direction, mine.yardage) : null;
      S.saving = false;
    }
    $("#open-kicker").textContent = playLabel(play);
    startTimer(play);
    paintPicks();
  }

  function paintPicks(errorText) {
    const expired = remaining() <= 0;
    const canPick = !expired && !!S.token;
    const { type, dir, yard } = S.selection;
    for (const [attr, value] of [["type", type], ["dir", dir], ["yard", yard]]) {
      for (const b of $$(`[data-${attr}]`)) {
        const on = b.dataset[attr] === value;
        b.classList.toggle("selected", on);
        b.setAttribute("aria-pressed", String(on));
        b.disabled = !canPick;
      }
    }
    const key = pickKey(type, dir, yard);
    const missing = [!type && "Run or Pass", !dir && "a direction", !yard && "how far"].filter(Boolean);
    if (errorText) setPickStatus(errorText, "error");
    else if (!S.token) setPickStatus("Sign in to make picks");
    else if (expired) setPickStatus("Time! Locking predictions…");
    else if (S.saving) setPickStatus("Sending your pick…");
    else if (key && key === S.savedKey) setPickStatus(`✓ Locked in: ${type} · ${dir} · ${yard}`, "saved");
    else if (missing.length === 3) setPickStatus("Make your call: type, direction, distance");
    else setPickStatus(`Now pick ${missing.join(" and ")}`);
  }

  function setPickStatus(text, kind = "") {
    const node = $("#pick-status");
    node.textContent = text;
    node.className = `pick-status ${kind}`;
  }

  function choose(kind, value) {
    if (remaining() <= 0 || !S.state || !S.state.play || S.state.play.state !== "OPEN") return;
    S.selection[kind] = value;
    if (navigator.vibrate) navigator.vibrate(8);
    if (S.selection.type && S.selection.dir && S.selection.yard) submitPick();
    paintPicks();
  }

  async function submitPick() {
    const { playId, type, dir, yard } = S.selection;
    const payload = { play_id: playId, play_type: type, direction: dir, yardage: yard };
    S.saving = true;
    if (socket && socket.send({ type: "predict", ...payload })) return;
    // Socket is down: fall back to plain HTTP so the pick still lands.
    try {
      onPredictionSaved(await api("/api/predictions", { method: "POST", token: S.token, body: payload }));
    } catch (err) {
      S.saving = false;
      paintPicks(err.message);
    }
  }

  function onPredictionSaved(pred) {
    if (!pred || pred.play_id !== S.selection.playId) return;
    S.saving = false;
    S.savedKey = pickKey(pred.play_type, pred.direction, pred.yardage);
    if (S.state) S.state.my_prediction = pred;
    if (S.view === "open") paintPicks();
  }

  function startTimer(play) {
    if (S.timer && S.timerPlayId === play.id) return;
    stopTimer();
    S.timerPlayId = play.id;
    const total = Math.max(1, play.locks_at - play.opened_at);
    const ring = $("#ring");
    const box = $("#countdown");
    const num = $("#countdown-num");
    const C = 2 * Math.PI * 36;
    ring.style.strokeDasharray = String(C);
    let lastShown = null;
    const tick = () => {
      const rem = remaining();
      const shown = Math.ceil(rem);
      ring.style.strokeDashoffset = String(C * (1 - rem / total));
      if (shown !== lastShown) {
        lastShown = shown;
        num.textContent = String(shown);
        box.classList.toggle("hurry", rem <= 7 && rem > 3);
        box.classList.toggle("critical", rem <= 3);
      }
      if (rem <= 0) {
        stopTimer();
        paintPicks();
      }
    };
    tick();
    S.timer = setInterval(tick, 100);
  }

  function stopTimer() {
    clearInterval(S.timer);
    S.timer = null;
    S.timerPlayId = null;
  }

  // -- LOCKED ----------------------------------------------------------------

  function pickChips(container, pick, play) {
    container.replaceChildren();
    if (!pick) {
      container.append(el("span", { class: "chip" }, "No pick this play"));
      return;
    }
    const graded = play && play.state === "RESOLVED" && !play.voided && "type_correct" in pick;
    const chip = (text, ok, what) =>
      el("span", {
        class: `chip ${graded ? (ok ? "good" : "bad") : ""}`,
        "aria-label": graded ? `${what} ${text}: ${ok ? "right" : "wrong"}` : null,
      }, graded ? `${text} ${ok ? "✓" : "✗"}` : text);
    container.append(
      el("span", { class: "chip" }, "Your pick"),
      chip(pick.play_type, pick.type_correct, "Play type"),
      chip(pick.direction, pick.direction_correct, "Direction"),
      // Picks made before distance picks existed have no distance.
      chip(pick.yardage || "NO DISTANCE", pick.yardage_correct, "Distance"),
    );
  }

  function renderLocked(st) {
    $("#locked-kicker").textContent = playLabel(st.play);
    pickChips($("#locked-pick"), st.my_prediction, st.play);
    const crowd = $("#locked-crowd");
    crowd.replaceChildren();
    const stats = st.crowd;
    if (stats && stats.total) {
      const typeBars = el("div", { class: "crowd" });
      const dirBars = el("div", { class: "crowd" });
      const yardBars = el("div", { class: "crowd" });
      crowd.append(
        el("div", { class: "pick-label", style: { marginBottom: "0" } }, `How ${players(stats.total)} called it`),
        typeBars,
        dirBars,
        yardBars,
      );
      PTP.crowdBars(typeBars, stats, ["RUN", "PASS"]);
      PTP.crowdBars(dirBars, stats, ["LEFT", "MIDDLE", "RIGHT"]);
      PTP.crowdBars(yardBars, stats, ["SHORT", "MEDIUM", "LONG"]);
    }
  }

  // -- RESOLVED --------------------------------------------------------------

  function renderResult(st) {
    const { play, my_prediction: pick, crowd } = st;
    const view = $('[data-view="result"]');
    const animate = st.event === "play_resolved" && S.animatedPlayId !== play.id;
    if (animate) S.animatedPlayId = play.id;

    const points = $("#result-points");
    const label = $("#result-label");
    $("#result-body").hidden = play.voided;
    $("#result-kicker").textContent = `${playLabel(play)} — ${play.voided ? "No play" : "Result"}`;

    if (play.voided) {
      points.textContent = "VOID";
      points.className = "points zero";
      label.textContent = "Play voided (penalty / no play) — no points";
      $("#result-pick").replaceChildren();
      $("#result-crowd").textContent = "";
    } else {
      $("#reveal-type").textContent = play.correct_play_type;
      $("#reveal-dir").textContent = play.correct_direction;
      const yardTile = $("#reveal-yard");
      yardTile.textContent = play.correct_yardage || "—";
      yardTile.closest(".reveal-tile").classList.toggle("loss", play.correct_yardage === "LOSS");
      $("#reveal-yards").textContent =
        play.yards_gained === null || play.yards_gained === undefined ? "" : yardsText(play.yards_gained);
      $("#result-body .reveal").setAttribute("aria-label", `The play: ${PTP.describeResult(play)}`);
      const pts = pick ? pick.points_earned || 0 : null;
      const right = partsRight(pick, st.scoring);
      points.textContent = pts === null ? "—" : `+${pts}`;
      points.className = `points ${right === 3 ? "exact" : pts ? "some" : "zero"}`;
      label.textContent = resultLabel(right);
      pickChips($("#result-pick"), pick, play);
      $("#result-crowd").textContent =
        crowd && crowd.total
          ? `${pct(crowd.exact, crowd.total)}% of ${players(crowd.total)} got all three · ${pct(crowd.scored, crowd.total)}% scored`
          : "";
    }

    if (animate) {
      view.classList.remove("animate");
      void view.offsetWidth; // restart CSS animations
      view.classList.add("animate");
      if (partsRight(pick, st.scoring) === 3) confetti();
      if (navigator.vibrate && pick && pick.points_earned) navigator.vibrate([20, 40, 20]);
    } else if (st.event !== "play_resolved") {
      view.classList.remove("animate");
    }
  }

  const players = (n) => `${n} ${n === 1 ? "player" : "players"}`;

  /**
   * How many of the three calls were right (0-3), or null with no pick. Uses the server's
   * per-part flags; without them, works it out from the points (10 a part, +10 bonus for all three).
   */
  function partsRight(pick, scoring) {
    if (!pick) return null;
    if ("type_correct" in pick) {
      return [pick.type_correct, pick.direction_correct, pick.yardage_correct].filter(Boolean).length;
    }
    const pts = pick.points_earned || 0;
    if (pts >= scoring.exact) return 3;
    return Math.min(2, Math.floor(pts / (scoring.type || 10)));
  }

  /** 3 right "Perfect call!" (40 with the bonus), 2 "Two of three" (20), 1 "One of three" (10), 0. */
  function resultLabel(right) {
    if (right === null) return "You didn't pick this play";
    return ["No points this time", "One of three", "Two of three", "Perfect call!"][right];
  }

  function confetti() {
    const host = $("#confetti");
    const css = getComputedStyle(document.documentElement);
    const colors = ["--home-1", "--home-2", "--away-2", "--gold", "--accent"].map((v) => css.getPropertyValue(v).trim());
    host.replaceChildren();
    for (let i = 0; i < 48; i += 1) {
      const bit = el("i");
      bit.style.left = `${Math.random() * 100}%`;
      bit.style.background = colors[i % colors.length];
      bit.style.animationDelay = `${0.4 + Math.random() * 0.5}s`;
      bit.style.setProperty("--dx", `${Math.round((Math.random() - 0.5) * 180)}px`);
      bit.style.setProperty("--rot", `${Math.round(180 + Math.random() * 720)}deg`);
      host.append(bit);
    }
    setTimeout(() => host.replaceChildren(), 3600);
  }

  // -- FINAL -----------------------------------------------------------------

  function renderFinal(st) {
    const { game, me } = st;
    $("#final-title").textContent = `Final: ${game.away_name} @ ${game.home_name}`;
    if (!me) {
      $("#final-text").textContent = "Thanks for watching!";
    } else if (me.rank) {
      let text = `You finished #${me.rank} of ${st.ranked_players} with ${me.game_score} points.`;
      if (st.lounge) {
        const row = st.lounge.leaderboard.find((r) => r.user_id === me.id);
        if (row) text += ` #${row.rank} in ${st.lounge.name}.`;
      }
      $("#final-text").textContent = text;
    } else {
      $("#final-text").textContent = "You didn't make any picks this game. Catch the next one!";
    }
  }

  // ------------------------------------------------------------------ me + board

  function renderMe(st, prev) {
    const box = $("#me-stats");
    box.hidden = !st.me;
    $("#whoami").hidden = !st.me;
    if (!st.me) return;
    $("#whoami-name").textContent = st.me.username;
    const set = (id, value, prevValue) => {
      const node = $(id);
      node.textContent = value;
      if (prevValue !== undefined && prevValue !== value) {
        node.classList.remove("bump");
        void node.offsetWidth;
        node.classList.add("bump");
      }
    };
    const p = prev && prev.me;
    set("#me-score", String(st.me.game_score), p ? String(p.game_score) : undefined);
    set("#me-rank", st.me.rank ? `#${st.me.rank}` : "—", p ? (p.rank ? `#${p.rank}` : "—") : undefined);
    set("#me-total", String(st.me.total_score), p ? String(p.total_score) : undefined);
  }

  function renderLoungeStrip(lounge) {
    $("#lounge-strip").hidden = !lounge;
    $("#board-tabs").hidden = !lounge;
    if (!lounge) {
      S.board = "global";
      return;
    }
    $("#lounge-name").textContent = lounge.name;
    $("#lounge-code").textContent = lounge.id;
    $("#lounge-count").textContent = `${lounge.member_count} ${lounge.member_count === 1 ? "player" : "players"}`;
    $("#lounge-tab").textContent = lounge.name;
  }

  function renderBoard() {
    const st = S.state;
    if (!st) return;
    const lounge = S.board === "lounge" ? st.lounge : null;
    for (const t of $$("[data-board]")) {
      const on = t.dataset.board === (lounge ? "lounge" : "global");
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", String(on));
    }
    const rows = lounge ? lounge.leaderboard : st.leaderboard;
    const moves = S.moves[lounge ? "lounge" : "global"];
    $("#board-title").textContent = lounge
      ? `${lounge.name} · Head-to-Head`
      : `Live Game Leaderboard${st.ranked_players ? ` · ${st.ranked_players} players` : ""}`;

    const board = $("#board");
    board.replaceChildren();
    if (!rows.length) {
      board.append(el("li", { class: "empty" }, st.game ? "No scores yet. Make your first pick!" : "Leaderboard opens at kickoff."));
      return;
    }
    const myId = st.me && st.me.id;
    const row = (r) => {
      const move = moves.get(r.user_id) || 0;
      return el("li", { class: `board-row${r.user_id === myId ? " me" : ""}${move > 0 ? " flash" : ""}` },
        el("span", { class: "board-rank" }, String(r.rank)),
        el("span", { class: "board-name" },
          el("strong", {}, r.is_host ? "👑 " : "", r.username, r.user_id === myId ? el("span", { class: "you-tag" }, "YOU") : null),
          el("span", {}, r.picks == null
            ? `${r.exact_hits} perfect`
            : `${r.picks} ${r.picks === 1 ? "pick" : "picks"} · ${r.exact_hits} perfect`)),
        el("span", { class: "board-score" },
          move ? el("span", { class: `move ${move > 0 ? "up" : "down"}` }, `${move > 0 ? "▲" : "▼"}${Math.abs(move)}`) : null,
          String(r.score)));
    };
    for (const r of rows) board.append(row(r));
    if (!lounge && st.me && st.me.rank && !rows.some((r) => r.user_id === myId)) {
      board.append(el("li", { class: "empty", style: { padding: "4px" } }, "⋯"));
      board.append(row({ user_id: myId, username: st.me.username, rank: st.me.rank, score: st.me.game_score,
        exact_hits: st.me.exact_hits, picks: null }));
    }
  }

  // ------------------------------------------------------------------ lounges UI

  async function openLoungeModal() {
    const modal = $("#lounge-modal");
    modal.hidden = false;
    $("#join-error").textContent = "";
    $("#create-error").textContent = "";
    if (!S.token) return;
    try {
      const { lounges } = await api("/api/me", { token: S.token });
      const list = $("#my-lounges");
      list.replaceChildren(...lounges.map((l) =>
        el("li", {}, el("a", { href: `/lounge/${l.id}` },
          l.name, el("span", { class: "num" }, `#${l.id} · ${l.member_count} ${l.member_count === 1 ? "player" : "players"}`)))));
      $("#my-lounges-section").hidden = !lounges.length;
    } catch { /* offline; the forms still work once back */ }
  }

  async function shareLounge(code, name) {
    const url = `${location.origin}/lounge/${code}`;
    const text = `Join my Pick the Play lounge "${name}" — code ${code}`;
    try {
      if (navigator.share) {
        await navigator.share({ title: "Pick the Play", text, url });
        return;
      }
      await navigator.clipboard.writeText(`${text}: ${url}`);
      toast("Invite link copied!", "success");
    } catch (err) {
      if (err && err.name === "AbortError") return;
      toast(`Share this code: ${code}`, "info", 5000);
    }
  }

  /** Deleting the account is how a name is changed: a name is only ever tied to this browser's sign-in. */
  async function changeName() {
    if (!S.token) return;
    const name = (S.user && S.user.username) || "your name";
    if (!confirm(`Change your name? This deletes "${name}" along with its picks, scores and any lounges you host, then lets you pick a new name.`)) return;
    const button = $("#change-name");
    button.disabled = true;
    try {
      await api("/api/me", { method: "DELETE", token: S.token });
    } catch (err) {
      if (err.status !== 401) {  // 401: the account is already gone, so carry on to the sign-in
        button.disabled = false;
        toast(err.message, "error");
        return;
      }
    }
    forgetToken();
    location.reload();
  }

  function wireUI() {
    $("#change-name").addEventListener("click", changeName);
    $("#announce-close").addEventListener("click", dismissAnnouncement);
    for (const b of $$("[data-type]")) b.addEventListener("click", () => choose("type", b.dataset.type));
    for (const b of $$("[data-dir]")) b.addEventListener("click", () => choose("dir", b.dataset.dir));
    for (const b of $$("[data-yard]")) b.addEventListener("click", () => choose("yard", b.dataset.yard));
    for (const t of $$("[data-board]")) {
      t.addEventListener("click", () => {
        S.board = t.dataset.board;
        renderBoard();
      });
    }

    const modal = $("#lounge-modal");
    $("#lounge-btn").addEventListener("click", openLoungeModal);
    modal.addEventListener("click", (ev) => {
      if (ev.target === modal || ev.target.closest("[data-close]")) modal.hidden = true;
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") modal.hidden = true;
    });

    $("#join-code").addEventListener("input", (ev) => {
      ev.target.value = ev.target.value.replace(/\D/g, "").slice(0, 4);
    });
    $("#join-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const code = $("#join-code").value;
      const error = $("#join-error");
      if (!/^\d{4}$/.test(code)) {
        error.textContent = "Enter the 4-digit code your friend shared.";
        return;
      }
      if (!S.token) {
        error.textContent = "Sign in first.";
        return;
      }
      try {
        await api(`/api/lounges/${code}/join`, { method: "POST", token: S.token });
        location.href = `/lounge/${code}`;
      } catch (err) {
        error.textContent = err.message;
      }
    });

    $("#create-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const error = $("#create-error");
      error.textContent = "";
      const name = $("#create-name").value.trim();
      if (name.length < 2) {
        error.textContent = "Give your lounge a name (2+ characters).";
        return;
      }
      if (!S.token) {
        error.textContent = "Sign in first.";
        return;
      }
      try {
        const lounge = await api("/api/lounges", { method: "POST", token: S.token, body: { name } });
        $("#created").hidden = false;
        $("#created-code").textContent = lounge.id;
        $("#go-created").href = `/lounge/${lounge.id}`;
        $("#share-created").onclick = () => shareLounge(lounge.id, lounge.name);
      } catch (err) {
        error.textContent = err.message;
      }
    });

    $("#invite-btn").addEventListener("click", () => {
      const lounge = S.state && S.state.lounge;
      if (lounge) shareLounge(lounge.id, lounge.name);
    });
  }

  init();
})();

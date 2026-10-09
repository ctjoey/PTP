/* Pick the Play — host console: Live data (the Tank01 play-by-play feed).
 *
 * Draws the Live data panel, the suggestion card, the disagreement banner, the "Pick today's game"
 * picker and the Fix result editor. admin.js creates it with PTPFeed.create(ctx) and calls
 * feed.render(state) for every admin_state. Everything here is optional: with no `feed` key in
 * admin_state (an older server) the panel stays hidden and the console works exactly as before.
 *
 * Protocol: admin_state.feed and the feed_* / correct_play actions (see README, "Live data").
 */
"use strict";

const PTPFeed = (() => {
  const { $, $$, el, toast, api, now, yardsText, bucketForYards } = PTP;

  const DEMO_ID = "demo";
  const QUIET_AFTER = 180; // seconds a play has been waited for before the panel turns amber
  const PARTS = [
    { key: "play_type", label: "play type", values: ["RUN", "PASS"] },
    { key: "direction", label: "direction", values: ["LEFT", "MIDDLE", "RIGHT"] },
    { key: "yardage", label: "distance", values: ["SHORT", "MEDIUM", "LONG", "LOSS"] },
  ];
  const STATE_LABEL = {
    off: "OFF", idle: "READY", waiting: "WAITING", paused: "PAUSED", capped: "LIMIT REACHED",
    error: "PROBLEM", not_started: "NOT STARTED", done: "DONE",
  };
  const TONE = {
    idle: "good", waiting: "good", paused: "wait", not_started: "wait", capped: "bad", error: "bad", off: "off", done: "off",
  };
  const DEFAULT_MESSAGE = {
    off: "Live data is off. You score every play by hand.",
    idle: "Ready. Checking starts when a play locks.",
    waiting: "Waiting for the feed to show this play.",
    paused: "Paused. Nothing is being checked.",
    capped: "Request limit reached. You score by hand.",
    error: "Can't reach the feed. You can score by hand.",
    not_started: "The game hasn't started yet.",
    done: "The game is finished.",
  };
  const BADGE = { ready: "FEED SUGGESTS", void: "FEED SAYS NO PLAY", review: "NEEDS YOUR CHECK", held: "ON HOLD" };

  const setText = (node, text) => { if (node.textContent !== text) node.textContent = text; };
  const pad = (n) => String(n).padStart(2, "0");
  const todayLocal = () => {
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  };

  /** "PASS · RIGHT · MEDIUM (7 yds)" from {play_type, direction, yardage, yards}. */
  function resultText(p) {
    const parts = [p.play_type, p.direction].filter(Boolean);
    if (p.yardage) parts.push(p.yards === null || p.yards === undefined ? p.yardage : `${p.yardage} (${yardsText(p.yards)})`);
    return parts.join(" · ");
  }

  /** The editable result of a Play log row, in the same shape. */
  const rowResult = (row) => ({
    play_type: row.correct_play_type || null,
    direction: row.correct_direction || null,
    yardage: row.correct_yardage || null,
    yards: row.yards_gained === undefined ? null : row.yards_gained,
  });

  /** Pull "PASS - LEFT - SHORT (7 yds)" apart again (the disagreement banner sends it as text). */
  function parseResultText(text) {
    const s = String(text || "").toUpperCase();
    const pick = (re) => { const m = re.exec(s); return m ? m[1] : null; };
    const yards = /(-?\d+)\s*YDS?\b/.exec(s);
    return {
      play_type: pick(/\b(RUN|PASS)\b/), direction: pick(/\b(LEFT|MIDDLE|RIGHT)\b/),
      yardage: pick(/\b(SHORT|MEDIUM|LONG|LOSS)\b/), yards: yards ? Number(yards[1]) : null,
    };
  }

  function create(ctx) {
    const S = {
      over: {}, overFor: null, // the host's choices for parts the feed couldn't fill in
      sgPlayId: null, sgStatus: null, activeId: null, doneFor: null, doneFixable: false,
      cardSig: null, bar: { key: null, total: 1 }, holdKey: null, live: { panel: null, card: null, disagree: null },
      ddKey: null, ddTouched: false, ddFromFeed: false, inflight: {},
      sched: { date: null, games: [], demo: null, loading: false, error: null, loaded: false },
      picked: null, fix: null, mounted: false,
    };
    const editor = $("#fix-editor");
    editor.remove(); // parked here until Fix result opens it inside a Play log row

    const state = () => ctx.getState();
    const feedOf = () => { const st = state(); return st && st.feed ? st.feed : null; };
    const busy = () => ctx.isBusy();
    const announce = (text) => setText($("#feed-live"), text);
    const baseTitle = document.title;
    /** A tab title that says "needs you" when a play is waiting on the host (they may be on another window). */
    const flagTitle = (on) => { const t = on ? `● Check the play · ${baseTitle}` : baseTitle; if (document.title !== t) document.title = t; };

    // ------------------------------------------------------------------ render

    function render(st) {
      const feed = st.feed || null;
      renderSetup(feed);
      renderPanel(st, feed);
      if (feed && st.game) {
        renderDisagreement(st, feed);
        renderCard(st, feed);
        renderScored(st, feed);
        renderNextDown(st, feed);
      }
      refreshPickers();
      updateTimers();
    }

    function toneOf(feed) {
      if (feed.state === "waiting" && isQuiet(feed)) return "wait";
      return TONE[feed.state] || "off";
    }

    function isQuiet(feed) {
      if (feed.state !== "waiting") return false;
      if (/quiet|long delay/i.test(feed.message || "")) return true;
      const w = feed.waiting;
      return !!(w && typeof w.since === "number" && now() - w.since > QUIET_AFTER);
    }

    function renderPanel(st, feed) {
      const panel = $("#feed-panel");
      const visible = !!(feed && st.game);
      panel.hidden = !visible;
      $("#feed-keys").hidden = !feed;
      if (!visible) { flagTitle(false); return; }
      const sg = feed.suggestion;
      // A locked play that live data is no longer watching (it joined late): only the host can score it.
      const stranded = !!st.play && st.play.state === "LOCKED" && !st.play.voided && !!feed.linked
        && feed.state === "idle" && !feed.waiting && !sg;
      flagTitle(!!feed.disagreement || !!(sg && (sg.status === "review" || sg.warning)) || stranded);

      const tone = stranded ? "wait" : toneOf(feed);
      const dot = $("#feed-dot");
      if (dot.dataset.tone !== tone) dot.dataset.tone = tone;
      dot.classList.toggle("pulse", feed.state === "waiting" && tone === "good");
      const chip = $("#feed-state");
      if (chip.dataset.tone !== tone) chip.dataset.tone = tone;
      const label = stranded ? "SCORE BY HAND" : isQuiet(feed) ? "QUIET"
        : (STATE_LABEL[feed.state] || String(feed.state || "").toUpperCase());
      setText(chip, label);
      const message = feed.message || DEFAULT_MESSAGE[feed.state] || "";
      setText($("#feed-msg"), message);

      // Announce state changes (not every poll) to screen readers.
      const settled = ["paused", "capped", "error", "not_started", "done"].includes(feed.state);
      const liveKey = `${feed.state}${settled ? `|${message}` : ""}`;
      if (S.live.panel !== liveKey) {
        if (S.live.panel !== null) announce(`Live data ${label.toLowerCase()}. ${message}`);
        S.live.panel = liveKey;
      }

      renderClock(feed);
      const linked = !!feed.linked;
      const b = busy();
      const pause = $("#feed-pause");
      pause.firstChild.nodeValue = `${feed.paused ? "Resume" : "Pause"} `;
      pause.disabled = b || !linked || feed.state === "off" || feed.state === "done";
      $("#feed-check").disabled = b || !linked || ["off", "done", "capped"].includes(feed.state);
      $("#feed-buttons").hidden = !linked;

      const r = feed.requests || {};
      const atGameCap = feed.state === "capped" && r.game_cap > 0 && r.game >= r.game_cap;
      const more = $("#feed-more");
      more.hidden = !atGameCap;
      more.disabled = b;

      renderUsage(feed, r, linked);
      renderLink(feed, linked);
      renderDetails(feed, linked);
    }

    function renderUsage(feed, r, linked) {
      $("#feed-usage").hidden = !linked;
      if (!linked) return;
      const cap = Number(r.game_cap) || 0;
      const used = Number(r.game) || 0;
      const pct = cap ? Math.min(100, Math.round((100 * used) / cap)) : 0;
      const lowPlan = r.plan_remaining !== null && r.plan_remaining !== undefined && r.plan_remaining <= 100;
      let text = `Requests this game ${used} / ${cap}`;
      if (r.plan_remaining !== null && r.plan_remaining !== undefined) text += ` · plan has ${r.plan_remaining} left`;
      if (pct >= 80) text += " (nearly at the limit)";
      else if (lowPlan) text += " (running low)";
      setText($("#feed-usage-text"), text);
      const meter = $("#feed-meter");
      meter.classList.toggle("warn", pct >= 80 || lowPlan);
      meter.setAttribute("aria-valuenow", String(pct));
      meter.setAttribute("aria-valuetext", text);
      meter.firstElementChild.style.width = `${pct}%`;
    }

    function renderLink(feed, linked) {
      const box = $("#feed-link");
      box.hidden = linked;
      if (linked) return;
      setText($("#feed-link-note"), feed.available
        ? "Follow a real game and the feed suggests each result for you."
        : "Live data isn't set up on this server (no Tank01 key), so you score by hand. You can still practice with the recorded game.");
      $("#feed-link-load").hidden = !feed.available;
      $("#feed-link-load").disabled = S.sched.loading;
      $("#feed-link-go").disabled = busy() || !$("#feed-link-select").value;
    }

    function renderDetails(feed, linked) {
      syncSwitch($("#feed-auto-score"), feed.auto_score);
      syncSwitch($("#feed-auto-open"), feed.auto_open);
      const lag = feed.lag || {};
      setText($("#feed-delay"), typeof lag.median === "number"
        ? `Typical delay: ${Math.round(lag.median)} s${lag.samples ? ` (from ${lag.samples} ${lag.samples === 1 ? "play" : "plays"})` : ""}`
        : "Typical delay: not measured yet");
      $("#feed-log").hidden = !linked;
      $("#feed-unlink").hidden = !linked;
    }

    /** Show the server's value unless the host's own toggle is still on its way to the server. */
    function syncSwitch(input, value) {
      if (S.inflight[input.id]) return;
      if (input.checked !== !!value) input.checked = !!value;
    }

    // ------------------------------------------------------------------ suggestion card

    /** The suggestion plus the parts the host filled in; says what is still missing. */
    function viewOf(sg) {
      const over = S.overFor === sg.play_id ? S.over : {};
      const merged = {
        play_type: sg.play_type || over.play_type || null,
        direction: sg.direction || over.direction || null,
        yardage: sg.yardage || over.yardage || bucketForYards(sg.yards) || null,
        yards: sg.yards === undefined ? null : sg.yards,
      };
      const isVoid = sg.status === "void" ||
        (sg.status === "held" && !sg.play_type && !sg.direction && /no play/i.test(sg.text || ""));
      const missing = isVoid ? [] : PARTS.filter((p) => !merged[p.key]);
      return { merged, isVoid, missing, complete: !isVoid && missing.length === 0 };
    }

    function renderCard(st, feed) {
      const sg = feed.suggestion;
      const card = $("#feed-card");
      card.hidden = !sg;
      if (!sg) {
        S.cardSig = null;
        return;
      }
      if (S.overFor !== sg.play_id) { S.over = {}; S.overFor = sg.play_id; }
      S.sgPlayId = sg.play_id;
      S.sgStatus = sg.status;
      const v = viewOf(sg);
      const status = v.isVoid && sg.status === "held" ? "held" : sg.status;
      if (card.dataset.status !== status) card.dataset.status = status;
      const autoRuns = (sg.status === "ready" || sg.status === "void") && typeof sg.auto_at === "number";

      const sig = JSON.stringify([sg, S.over, !!feed.paused]);
      if (sig !== S.cardSig) {
        S.cardSig = sig;
        setText($("#sg-badge"), BADGE[sg.status] || "FEED SUGGESTS");
        setText($("#sg-clock"), [sg.clock, sg.down_and_distance].filter(Boolean).join(" · "));
        setText($("#sg-text"), sg.text || "");
        renderChips(sg, v);
        $("#sg-flags").replaceChildren(...(sg.flags || []).map((f) => el("li", {}, f)));
        $("#sg-flags").hidden = !(sg.flags && sg.flags.length);
        const warning = $("#sg-warning");
        warning.hidden = !sg.warning;
        setText(warning, sg.warning || "");
        renderNote(sg, v, autoRuns, !!feed.paused);
      }
      $("#sg-timer").hidden = !autoRuns;

      const b = busy();
      const score = $("#sg-score");
      score.firstChild.nodeValue = `${v.isVoid ? "Void play" : "Score now"} `;
      score.disabled = b || (!v.isVoid && !v.complete);
      score.querySelector("kbd").hidden = !(sg.status === "ready" || sg.status === "void");
      const hold = $("#sg-hold");
      hold.hidden = !autoRuns;
      hold.disabled = b;
      $("#sg-change").disabled = b;
      const skip = $("#sg-skip");
      skip.hidden = !(sg.warning || sg.status === "review" || sg.status === "held");
      skip.disabled = b;

      announceCard(sg, v, autoRuns);
    }

    function renderNote(sg, v, autoRuns, paused) {
      const note = $("#sg-note");
      let text = "";
      if (sg.status === "review") {
        text = v.missing.length
          ? `Pick the ${v.missing.map((p) => p.label).join(", ").replace(/, ([^,]*)$/, " and $1")} above, then Score.`
          : "An unusual play, so it won't score by itself. Check it, then tap Score.";
      } else if (sg.status === "held") {
        text = v.isVoid
          ? "On hold. Tap Void play to confirm it, or score it yourself with Change."
          : "On hold. Nothing is scored until you tap Score now.";
      } else if (!autoRuns) {
        text = paused
          ? "Live data is paused, so this won't score by itself. Tap Score now, or Resume."
          : "Auto-score is off. Tap Score now when you're happy with it.";
      }
      note.hidden = !text;
      setText(note, text);
    }

    /** RUN / LEFT / MEDIUM chips as the host knows them; missing parts become a small picker. */
    function renderChips(sg, v) {
      const box = $("#sg-chips");
      if (v.isVoid) {
        box.replaceChildren(el("span", { class: "rchip" }, "NO PLAY"));
        return;
      }
      const over = S.overFor === sg.play_id ? S.over : {};
      const nodes = [];
      for (const part of PARTS) {
        const value = v.merged[part.key];
        if (value) {
          const sub = part.key === "yardage" && v.merged.yards !== null ? yardsText(v.merged.yards) : null;
          const chosen = !!over[part.key] && !sg[part.key];
          nodes.push(el("span", { class: `rchip${value === "LOSS" ? " loss" : ""}${chosen ? " chosen" : ""}` }, value,
            sub ? el("small", {}, ` ${sub}`) : null));
          continue;
        }
        nodes.push(el("div", { class: "ask", role: "group", "aria-label": `Pick the ${part.label}` },
          el("span", { class: "ask-label" }, `Pick the ${part.label}`),
          el("div", { class: "ask-row" }, part.values.map((val) =>
            el("button", {
              class: "mini-btn", type: "button", onclick: () => pickMissing(sg, part.key, val),
            }, val)))));
      }
      box.replaceChildren(...nodes);
    }

    /** The host chose a missing part: show it as a chip and move on to the next gap (or Score). */
    function pickMissing(sg, key, value) {
      S.over[key] = value;
      S.overFor = sg.play_id;
      render(state());
      const next = $("#sg-chips .mini-btn") || $("#sg-score");
      if (next && !next.disabled) next.focus();
    }

    function announceCard(sg, v, autoRuns) {
      const key = `${sg.play_id}|${sg.status}|${sg.text}`;
      if (S.live.card === key) return;
      S.live.card = key;
      const what = v.isVoid ? "Feed says no play." : `Feed suggests ${resultText(v.merged) || "a play to check"}.`;
      const next = sg.status === "review" ? "It needs your check."
        : sg.status === "held" ? "On hold."
          : autoRuns ? "It will be applied automatically. Press H to hold it." : "Waiting for you to score it.";
      announce(`${what} ${sg.warning ? `${sg.warning}. ` : ""}${next}`);
    }

    /** Score now / Void play: apply the suggestion (plus whatever the host filled in). */
    async function scoreNow() {
      const sg = feedOf() && feedOf().suggestion;
      if (!sg) return;
      const v = viewOf(sg);
      if (!v.isVoid && !v.complete) return;
      const payload = { play_id: sg.play_id };
      if (v.isVoid) payload.void = true;
      else for (const part of PARTS) if (!sg[part.key] && S.over[part.key]) payload[part.key] = S.over[part.key];
      await ctx.act("feed_accept", payload);
    }

    /** Change...: stop the timer, then load the suggestion into the manual result controls. */
    async function change() {
      const sg = feedOf() && feedOf().suggestion;
      if (!sg) return;
      if ((sg.status === "ready" || sg.status === "void") && typeof sg.auto_at === "number") await ctx.act("feed_hold");
      const v = viewOf(sg);
      ctx.loadResult(v.isVoid ? {} : v.merged);
    }

    /** The host started choosing a result by hand: stop the suggestion's timer so it can't race them. */
    function manualEdit() {
      const sg = feedOf() && feedOf().suggestion;
      if (!sg || !(sg.status === "ready" || sg.status === "void") || typeof sg.auto_at !== "number") return;
      const key = `${sg.play_id}:${sg.auto_at}`;
      if (S.holdKey === key) return;
      S.holdKey = key;
      ctx.act("feed_hold");
    }

    // ------------------------------------------------------------------ banner + confirmation

    function renderDisagreement(st, feed) {
      const d = feed.disagreement;
      const box = $("#feed-disagree");
      box.hidden = !d;
      if (!d) return;
      const row = (st.history || []).find((h) => h.id === d.play_id);
      setText($("#fd-title"), row ? `The feed disagrees with play #${row.play_number}` : "The feed disagrees with a scored play");
      setText($("#fd-body"), `Feed says ${d.feed}, but this play was scored ${d.scored}.`);
      const quote = $("#fd-text");
      quote.hidden = !d.text;
      setText(quote, d.text ? `"${d.text}"` : "");
      $("#fd-fix").disabled = busy() || !(row && row.state === "RESOLVED" && !row.voided);
      $("#fd-dismiss").disabled = busy();
      const key = `${d.play_id}|${d.feed}|${d.scored}`;
      if (S.live.disagree !== key) {
        S.live.disagree = key;
        announce(`The feed disagrees with a scored play. Feed says ${d.feed}, but it was scored ${d.scored}. Fix result or dismiss.`);
      }
    }

    /** The confirmation line for the play the feed just scored (its own words), with Fix result. */
    function scoredInfo(st, feed) {
      const play = st.play;
      if (!play || play.state !== "RESOLVED") return null;
      const row = (st.history || []).find((h) => h.id === play.id) || {};
      if (row.resolved_by === "host-fix") return null; // corrected since: the Play log marks it
      const ls = feed.last_scored;
      if (ls && ls.play_id === play.id) {
        const voided = !!(ls.voided || play.voided);
        return {
          id: play.id, fixable: !voided,
          text: voided ? "Voided by the feed: no play." : `${ls.auto ? "Scored by the feed" : "Scored from the feed"}: ${ls.summary}`,
        };
      }
      if (ls !== undefined) return null; // the server reports it and this play isn't the feed's
      // A server that doesn't send last_scored: infer it from the suggestion we watched.
      const sawSuggestion = S.sgPlayId === play.id;
      const viaFeed = row.resolved_by ? row.resolved_by === "feed" : sawSuggestion;
      const voidedByFeed = !!play.voided && sawSuggestion && S.sgStatus === "void";
      if (!(S.activeId === play.id || sawSuggestion) || !((!play.voided && viaFeed) || voidedByFeed)) return null;
      return {
        id: play.id, fixable: !play.voided,
        text: play.voided ? "Voided by the feed: no play." : `Scored by the feed: ${resultText(rowResult({ ...row, ...play }))}`,
      };
    }

    function renderScored(st, feed) {
      const play = st.play;
      if (play && play.state !== "RESOLVED") S.activeId = play.id;
      const box = $("#feed-scored");
      const info = scoredInfo(st, feed);
      if (info && S.doneFor !== info.id) {
        S.doneFor = info.id;
        announce(info.text);
      }
      if (!info) S.doneFor = null;
      box.hidden = !info;
      if (!info) return;
      S.doneFixable = info.fixable;
      setText($("#fs-text"), info.text);
      $("#fs-fix").hidden = !info.fixable;
    }

    // ------------------------------------------------------------------ game clock

    /** The feed's game clock and the play the app is on, to hold against the TV. Updates with every check. */
    function renderClock(feed) {
      const c = feed.clock;
      const box = $("#game-clock");
      const show = !!(feed.linked && c && (c.feed || c.app));
      box.hidden = !show;
      if (!show) return;
      setText($("#gc-feed"), c.feed || "-");
      setText($("#gc-app"), c.app || "-");
      const behind = c.behind || 0;
      const node = $("#gc-behind");
      setText(node, behind > 0
        ? `${behind} ${behind === 1 ? "play" : "plays"} ahead of the app: press Check now to catch up`
        : "In step with the feed");
      node.classList.toggle("late", behind > 0);
      clockAge(feed);
    }

    function clockAge(feed) {
      const c = feed && feed.clock;
      if (!c || typeof c.feed_at !== "number") { setText($("#gc-age"), ""); return; }
      const age = Math.max(0, Math.round(now() - c.feed_at));
      setText($("#gc-age"), age < 90 ? `as of ${age} s ago` : `as of ${Math.round(age / 60)} min ago (it only checks while a play is locked)`);
    }

    // ------------------------------------------------------------------ next play prefill

    /** Fill Down and To go from the feed's next_down until the host edits them. */
    function renderNextDown(st, feed) {
      const nd = feed.next_down;
      const play = st.play;
      const active = !!play && play.state !== "RESOLVED";
      if (active) S.ddTouched = false; // a new play is open: the host's next edit is about the next one
      const hint = $("#dd-hint");
      if (!nd) {
        hint.hidden = true;
        S.ddFromFeed = false;
        return;
      }
      const key = `${st.game.id}:${play ? play.id : 0}:${nd.down}:${nd.distance}`;
      if (!active && !S.ddTouched && S.ddKey !== key) {
        S.ddKey = key;
        ctx.applyNextDown(nd);
        S.ddFromFeed = true;
      }
      hint.hidden = !(S.ddFromFeed && !S.ddTouched && !active);
    }

    function ddEdited() {
      S.ddTouched = true;
      S.ddFromFeed = false;
      $("#dd-hint").hidden = true;
    }

    // ------------------------------------------------------------------ timers

    function updateTimers() {
      const st = state();
      const feed = st && st.feed;
      if (!feed || !st.game || $("#feed-panel").hidden) return;
      const t = now();
      clockAge(feed);
      const sg = feed.suggestion;
      if (sg && typeof sg.auto_at === "number" && (sg.status === "ready" || sg.status === "void")) {
        const rem = sg.auto_at - t;
        const key = `${sg.play_id}:${sg.auto_at}`;
        if (S.bar.key !== key) S.bar = { key, total: Math.max(rem, 1) };
        const verb = sg.status === "void" ? "Voiding" : "Scoring";
        setText($("#sg-timer-text"), rem > 0.05 ? `${verb} in ${Math.ceil(rem)} s` : `${verb} now…`);
        $("#sg-bar").style.width = `${Math.max(0, Math.min(100, (100 * rem) / S.bar.total))}%`;
      }
      let sub = "";
      if (typeof feed.auto_open_at === "number" && feed.auto_open_at > t - 1) {
        sub = `Next play opens automatically in ${Math.max(0, Math.ceil(feed.auto_open_at - t))} s. Pause cancels it.`;
      } else if (feed.state === "waiting" && feed.waiting) {
        const w = feed.waiting;
        const bits = [];
        if (typeof w.since === "number") bits.push(`waiting ${Math.max(0, Math.round(t - w.since))} s`);
        if (w.checks) bits.push(`${w.checks} ${w.checks === 1 ? "check" : "checks"} so far`);
        if (typeof w.next_check_at === "number") bits.push(`next check in ${Math.max(0, Math.ceil(w.next_check_at - t))} s`);
        sub = bits.join(" · ");
        if (sub) sub = sub.charAt(0).toUpperCase() + sub.slice(1);
      }
      const subNode = $("#feed-sub");
      subNode.hidden = !sub;
      setText(subNode, sub);
      // The state chip can turn QUIET purely with time.
      if (feed.state === "waiting") {
        const quiet = isQuiet(feed);
        setText($("#feed-state"), quiet ? "QUIET" : STATE_LABEL.waiting);
        const tone = quiet ? "wait" : "good";
        if ($("#feed-dot").dataset.tone !== tone) $("#feed-dot").dataset.tone = tone;
        $("#feed-state").dataset.tone = tone;
      }
    }

    // ------------------------------------------------------------------ game picker

    function renderSetup(feed) {
      const box = $("#pick-game");
      box.hidden = !feed;
      if (!feed) return;
      const btn = $("#pick-game-btn");
      const label = feed.available ? "Pick today's game" : "Practice with a recorded game";
      setText(btn, label);
      btn.setAttribute("aria-expanded", String(!$("#pick-game-panel").hidden));
      setText($("#pick-note"), feed.available ? "" : "Live data isn't set up on this server (no Tank01 key). Only the recorded practice game is offered.");
      $("#pick-note").hidden = feed.available;
      $("#pick-date-row").hidden = !feed.available;
    }

    function demoGame() {
      if (S.sched.demo && S.sched.demo.away && S.sched.demo.home) return S.sched.demo;
      const presets = (() => { try { return JSON.parse($("#team-presets").textContent) || []; } catch { return []; } })();
      const team = (name, abbr) => {
        const p = presets.find((t) => t.name === name);
        return { abbr, name, primary: p ? p.primary : "#444444", secondary: p ? p.secondary : "#bbbbbb" };
      };
      return { feed_game_id: DEMO_ID, away: team("Carolina", "CAR"), home: team("Washington", "WSH"), time: "", status: "Recorded" };
    }

    const gameLabel = (g) => {
      const a = g.away.abbr || g.away.name, h = g.home.abbr || g.home.name;
      const status = g.status && g.status !== "Scheduled" ? ` (${g.status})` : "";
      return `${a} at ${h}${g.time ? ` - ${g.time}` : ""}${status}`;
    };

    /** (Re)build a game dropdown only when its contents change, so a half-made choice survives. */
    function fillOptions(select) {
      const feed = feedOf();
      const sig = JSON.stringify([S.sched.games.map((g) => g.feed_game_id), !!S.sched.demo, S.sched.loading, feed && feed.available]);
      if (select.dataset.sig === sig) return;
      select.dataset.sig = sig;
      const keep = select.value;
      const options = [el("option", { value: "" }, S.sched.loading ? "Loading games…" : "Choose a game…")];
      S.sched.games.forEach((g, i) => options.push(el("option", { value: String(i) }, gameLabel(g))));
      options.push(el("option", { value: DEMO_ID }, "Practice with a recorded game (no requests used)"));
      select.replaceChildren(...options);
      select.value = Array.from(select.options).some((o) => o.value === keep) ? keep : "";
      select.disabled = S.sched.loading;
    }

    const gameFor = (value) => (value === DEMO_ID ? demoGame() : S.sched.games[Number(value)] || null);

    function refreshPickers() {
      if (!feedOf()) return;
      fillOptions($("#pick-select"));
      fillOptions($("#feed-link-select"));
      $("#pick-load").disabled = S.sched.loading;
      const status = $("#pick-status");
      let text = "";
      if (S.sched.loading) text = "Loading the schedule…";
      else if (S.sched.error) text = `${S.sched.error} You can still pick the practice game.`;
      else if (S.sched.loaded) {
        const n = S.sched.games.length;
        const used = S.sched.cached ? "from a recent check, no new request" : "1 request used";
        text = n ? `${n} ${n === 1 ? "game" : "games"} found for ${S.sched.date} (${used}).`
          : `No games found for ${S.sched.date}. Try another date, or pick the practice game.`;
      }
      status.hidden = !text;
      status.classList.toggle("error", !!S.sched.error);
      setText(status, text);
      const linkStatus = $("#feed-link-status");
      linkStatus.hidden = !text;
      linkStatus.classList.toggle("error", !!S.sched.error);
      setText(linkStatus, text);
      const f = feedOf();
      if (f && !f.linked) $("#feed-link-go").disabled = busy() || !$("#feed-link-select").value;
    }

    async function loadSchedule() {
      const feed = feedOf();
      if (!feed || !feed.available || S.sched.loading) return;
      const iso = $("#pick-date").value || todayLocal();
      $("#pick-date").value = iso;
      S.sched.loading = true;
      S.sched.error = null;
      refreshPickers();
      try {
        const data = await api(`/api/admin/feed/games?date=${iso.replaceAll("-", "")}`, { adminKey: ctx.getKey() });
        const problem = data && typeof data.error === "string" && data.error ? data.error : null;
        S.sched = {
          ...S.sched, date: iso, games: Array.isArray(data && data.games) ? data.games : [], cached: !!(data && data.cached),
          demo: data && data.demo && data.demo.away && data.demo.home ? data.demo : null, error: problem, loaded: !problem,
        };
      } catch (err) {
        const why = err.status === 401 ? "The admin key was rejected." : `${String(err.message || "No answer from the server").replace(/\.$/, "")}.`;
        S.sched = { ...S.sched, games: [], error: `Couldn't load the schedule: ${why}`, loaded: false };
      }
      S.sched.loading = false;
      refreshPickers();
    }

    function fillTeam(side, team) {
      const form = $("#game-form");
      const set = (k, v) => {
        const field = form.elements[`${side}_${k}`];
        field.value = v;
        field.dispatchEvent(new Event("input", { bubbles: true }));
      };
      set("name", team.name);
      set("primary", String(team.primary).toLowerCase());
      set("secondary", String(team.secondary).toLowerCase());
    }

    /** A game picked in Game Setup: fill the form and remember the feed id for Create Game. */
    function choose(game) {
      fillTeam("away", game.away);
      fillTeam("home", game.home);
      $("#feed-game-id").value = game.feed_game_id;
      S.picked = game;
      const when = game.time ? ` - ${game.time}` : "";
      setText($("#pick-picked-text"), game.feed_game_id === DEMO_ID
        ? "Practice game: Carolina at Washington (no requests used). Tap Create Game to start it."
        : `Live data: ${gameLabel({ ...game, time: "", status: "" })}${when}. Tap Create Game to start it.`);
      $("#pick-picked").hidden = false;
      $("#pick-game-panel").hidden = true;
      $("#pick-game-btn").setAttribute("aria-expanded", "false");
    }

    function clearPick() {
      $("#feed-game-id").value = "";
      S.picked = null;
      $("#pick-picked").hidden = true;
      $("#pick-select").value = "";
    }

    function pickButton() {
      const feed = feedOf();
      if (!feed) return;
      if (!feed.available) { choose(demoGame()); return; }
      const panel = $("#pick-game-panel");
      panel.hidden = !panel.hidden;
      $("#pick-game-btn").setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) {
        if (!$("#pick-date").value) $("#pick-date").value = todayLocal();
        if (!S.sched.loaded && !S.sched.loading) loadSchedule();
      }
    }

    // ------------------------------------------------------------------ Fix result

    const fixComplete = (f) => !!(f.play_type && f.direction && f.yardage);

    function fixChanged(f) {
      const row = ((state() || {}).history || []).find((h) => h.id === f.playId);
      if (!row) return false;
      const cur = rowResult(row);
      const same = cur.play_type === f.play_type && cur.direction === f.direction && cur.yardage === f.yardage &&
        (f.yards === null || f.yards === undefined || cur.yards === f.yards);
      return !same;
    }

    function openFix(playId, preset = null) {
      const st = state();
      const row = ((st && st.history) || []).find((h) => h.id === playId);
      if (!row || row.state !== "RESOLVED" || row.voided) {
        toast("Only a scored play can be fixed.", "error");
        return;
      }
      const base = rowResult(row);
      const use = (k) => (preset && preset[k] !== null && preset[k] !== undefined ? preset[k] : base[k]);
      S.fix = { playId, play_type: use("play_type"), direction: use("direction"), yardage: use("yardage"), yards: use("yards") };
      ctx.refreshHistory();
      const first = editor.querySelector("[data-fx] .selected") || editor.querySelector("[data-fx] button");
      if (first) first.focus();
      editor.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }

    function closeFix(refocus = true) {
      const id = S.fix && S.fix.playId;
      S.fix = null;
      ctx.refreshHistory();
      if (refocus && id) {
        const btn = document.querySelector(`[data-fix="${id}"]`);
        if (btn) btn.focus();
      }
    }

    function paintEditor() {
      const f = S.fix;
      if (!f) return;
      const row = ((state() || {}).history || []).find((h) => h.id === f.playId);
      setText($("#fix-title", editor), `Fix result for play #${row ? row.play_number : "?"}`);
      for (const group of $$("[data-fx]", editor)) {
        for (const b of $$("button", group)) {
          const on = b.dataset.v === f[group.dataset.fx];
          b.classList.toggle("selected", on);
          b.setAttribute("aria-pressed", String(on));
        }
      }
      const yards = $("#fix-yards", editor);
      const want = f.yards === null || f.yards === undefined ? "" : String(f.yards);
      if (document.activeElement !== yards && yards.value !== want) yards.value = want;
      $("#fix-save", editor).disabled = busy() || !fixComplete(f) || !fixChanged(f);
    }

    async function saveFix() {
      const f = S.fix;
      if (!f || !fixComplete(f) || !fixChanged(f)) return;
      const payload = { play_id: f.playId, play_type: f.play_type, direction: f.direction, yardage: f.yardage };
      if (f.yards !== null && f.yards !== undefined) payload.yards = f.yards;
      const ack = await ctx.act("correct_play", payload);
      if (ack && ack.ok) {
        toast(`Corrected: ${resultText(f)}`, "success");
        closeFix(true);
      }
    }

    /** Extras for a Play log row: marker tags and the Fix button. */
    function rowExtras(p) {
      const tags = [];
      if (p.resolved_by === "host-fix") tags.push(el("span", { class: "tag fixed", title: "The result was corrected after scoring" }, "corrected"));
      else if (p.resolved_by === "feed" && !p.voided) tags.push(el("span", { class: "tag", title: "Scored automatically from the live feed" }, "auto"));
      const fixable = !!feedOf() && p.state === "RESOLVED" && !p.voided;
      const action = fixable
        ? el("button", {
          class: "link-btn fix-link", type: "button", "data-fix": p.id,
          "aria-label": `Fix result for play ${p.play_number}`, onclick: () => openFix(p.id),
        }, "Fix result")
        : null;
      return { tags, action };
    }

    function mountEditor(li, p) {
      if (!S.fix || S.fix.playId !== p.id) return;
      li.append(editor);
      editor.hidden = false;
      S.mounted = true;
      paintEditor();
    }

    /** Called after the Play log is rebuilt: forget a Fix whose row is gone. */
    function afterHistory() {
      if (S.fix && !S.mounted) S.fix = null;
      if (!S.fix) editor.hidden = true;
      S.mounted = false;
    }

    // ------------------------------------------------------------------ other actions

    async function downloadLog() {
      const st = state();
      if (!st || !st.game) return;
      try {
        const data = await api(`/api/admin/feed/log?game_id=${st.game.id}`, { adminKey: ctx.getKey() });
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = el("a", { href: url, download: `feed-log-game-${st.game.id}.json` });
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 4000);
      } catch (err) {
        toast(`Couldn't download the feed log: ${err.message}`, "error", 4500);
      }
    }

    async function setSwitch(input, field) {
      S.inflight[input.id] = true;
      const ack = await ctx.act("feed_set", { [field]: input.checked });
      S.inflight[input.id] = false;
      if (!ack || !ack.ok) input.checked = !input.checked; // the server said no: show the truth again
      render(state());
    }

    // ------------------------------------------------------------------ keyboard

    /** Shift+P pauses/resumes, H holds a suggestion. Returns true when the key was used. */
    function handleKey(ev) {
      if (!feedOf() || $("#feed-panel").hidden) return false;
      const key = ev.key.toLowerCase();
      if (ev.shiftKey && key === "p") {
        if ($("#feed-buttons").hidden) return false; // not following a game: Shift+P stays PASS, as before
        const btn = $("#feed-pause");
        if (!btn.disabled) btn.click();
        return true;
      }
      if (!ev.shiftKey && key === "h") {
        const btn = $("#sg-hold");
        if (!$("#feed-card").hidden && !btn.hidden && !btn.disabled) btn.click();
        return true;
      }
      return false;
    }

    /** Enter while a suggestion is ready: Score now. */
    function scoreFromKey() {
      const sg = feedOf() && feedOf().suggestion;
      if (!sg || !(sg.status === "ready" || sg.status === "void") || $("#feed-card").hidden) return false;
      const btn = $("#sg-score");
      if (btn.disabled) return false;
      btn.click();
      return true;
    }

    // ------------------------------------------------------------------ wiring

    function wire() {
      $("#feed-pause").addEventListener("click", () => {
        const feed = feedOf();
        if (feed) ctx.act(feed.paused ? "feed_resume" : "feed_pause");
      });
      $("#feed-check").addEventListener("click", () => ctx.act("feed_check_now"));
      $("#feed-more").addEventListener("click", () => ctx.act("feed_allow_more", { n: 100 }));
      $("#feed-auto-score").addEventListener("change", (ev) => setSwitch(ev.target, "auto_score"));
      $("#feed-auto-open").addEventListener("change", (ev) => setSwitch(ev.target, "auto_open"));
      $("#feed-log").addEventListener("click", downloadLog);
      $("#feed-unlink").addEventListener("click", () => {
        if (confirm("Turn off live data for this game? You can follow a game again later.")) ctx.act("feed_link", { feed_game_id: null });
      });

      $("#sg-score").addEventListener("click", scoreNow);
      $("#sg-hold").addEventListener("click", () => ctx.act("feed_hold"));
      $("#sg-change").addEventListener("click", change);
      $("#sg-skip").addEventListener("click", () => {
        const sg = feedOf() && feedOf().suggestion;
        if (sg) ctx.act("feed_skip", { play_id: sg.play_id });
      });
      $("#fd-dismiss").addEventListener("click", () => ctx.act("feed_dismiss"));
      $("#fd-fix").addEventListener("click", () => {
        const d = feedOf() && feedOf().disagreement;
        if (d) openFix(d.play_id, d.feed_result || parseResultText(d.feed));
      });
      $("#fs-fix").addEventListener("click", () => { if (S.doneFor) openFix(S.doneFor); });

      $("#pick-game-btn").addEventListener("click", pickButton);
      $("#pick-load").addEventListener("click", loadSchedule);
      $("#pick-date").addEventListener("change", () => { S.sched.loaded = false; });
      $("#pick-select").addEventListener("change", (ev) => {
        const game = ev.target.value ? gameFor(ev.target.value) : null;
        if (game) choose(game);
      });
      $("#pick-clear").addEventListener("click", () => { clearPick(); $("#pick-game-btn").focus(); });

      $("#feed-link-load").addEventListener("click", () => {
        if (!$("#pick-date").value) $("#pick-date").value = todayLocal();
        loadSchedule();
      });
      $("#feed-link-select").addEventListener("change", refreshPickers);
      $("#feed-link-go").addEventListener("click", async () => {
        const game = gameFor($("#feed-link-select").value);
        if (!game) return;
        const ack = await ctx.act("feed_link", { feed_game_id: game.feed_game_id });
        if (ack && ack.ok) toast(game.feed_game_id === DEMO_ID ? "Following the recorded practice game." : "Live data linked to this game.", "success");
      });

      for (const group of $$("[data-fx]", editor)) {
        for (const b of $$("button", group)) {
          b.addEventListener("click", () => {
            if (!S.fix) return;
            S.fix[group.dataset.fx] = b.dataset.v;
            if (group.dataset.fx === "yardage" && S.fix.yards !== null && bucketForYards(S.fix.yards) !== b.dataset.v) S.fix.yards = null;
            paintEditor();
          });
        }
      }
      const yards = $("#fix-yards", editor);
      yards.addEventListener("input", () => {
        if (!S.fix) return;
        const raw = yards.value.trim();
        const n = Number(raw);
        if (raw === "") S.fix.yards = null;
        else if (Number.isInteger(n) && n >= -99 && n <= 99) {
          S.fix.yards = n;
          S.fix.yardage = bucketForYards(n);
        }
        paintEditor();
      });
      editor.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") { ev.preventDefault(); closeFix(true); }
        else if (ev.key === "Enter" && ev.target === yards) { ev.preventDefault(); saveFix(); }
      });
      $("#fix-save", editor).addEventListener("click", saveFix);
      $("#fix-cancel", editor).addEventListener("click", () => closeFix(true));

      setInterval(updateTimers, 250);
    }
    wire();

    return {
      render, handleKey, scoreFromKey, manualEdit, ddEdited, rowExtras, mountEditor, afterHistory,
      fixSig: () => (S.fix ? S.fix.playId : 0),
      /** Called after Create Game succeeds: forget the picked game; says whether it was linked. */
      afterCreate() {
        const linked = !!S.picked;
        const demo = linked && S.picked.feed_game_id === DEMO_ID;
        clearPick();
        return linked ? (demo ? "demo" : "live") : null;
      },
      picked: () => S.picked,
    };
  }

  return { create };
})();

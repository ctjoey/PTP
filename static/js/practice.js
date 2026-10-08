/* Pick the Play — practice round on the player page (and lounge pages).
 *
 * Simulated plays with the live game's 15-second timer, three-part pick and scoring, so anyone can try the
 * game with no game on and without signing in. Nothing here talks to the server. The scoring and the odds are
 * the same as the iPhone app's Practice mode: +10 play type, +10 direction, +10 distance, +10 bonus for all
 * three = 40; a loss of yards never matches a distance pick.
 */
"use strict";

(() => {
  const { $, $$, el, downDistance, bucketForYards, yardsText, titleCase } = PTP;
  const modal = $("#practice-modal");
  if (!modal) return;

  const WINDOW_SECONDS = 15;
  const LOCK_PAUSE_MS = 1400;
  const POINTS = { type: 10, direction: 10, distance: 10, bonus: 10 };
  const RING = 2 * Math.PI * 36;

  const G = {
    play: 0, down: 1, toGo: 10, lastYards: null,
    phase: "idle",                       // idle | open | locked | result
    pick: { type: null, dir: null, yard: null },
    deadline: 0,
    ticker: null, pause: null,
    score: 0, played: 0, perfect: 0,
    opener: null,
  };

  // ------------------------------------------------------------------ the simulated game

  const rand = () => Math.random();
  const randInt = (lo, hi) => lo + Math.floor(rand() * (hi - lo + 1));

  /**
   * Yards gained, by a uniform `roll` in 0..1.
   * Runs: 10% loss, 58% 0-5, 22% 6-10, 10% 11+.
   * Passes: 37% incomplete (0 yds = short), 18% 1-5, 21% 6-10, 24% 11+ (no sacks: a sack is no play).
   */
  function randomYards(type, roll = rand()) {
    if (type === "RUN") {
      if (roll < 0.10) return -randInt(1, 4);
      if (roll < 0.68) return randInt(0, 5);
      if (roll < 0.90) return randInt(6, 10);
      return randInt(11, 45);
    }
    if (roll < 0.37) return 0;
    if (roll < 0.55) return randInt(1, 5);
    if (roll < 0.76) return randInt(6, 10);
    return randInt(11, 60);
  }

  /** Rough pro tendencies: slightly more passes, and runs spread across the three directions. */
  function randomOutcome() {
    const type = rand() < 0.56 ? "PASS" : "RUN";
    const roll = rand();
    const dir = roll < 0.36 ? "LEFT" : roll < 0.64 ? "MIDDLE" : "RIGHT";
    const yards = randomYards(type);
    return { type, dir, yards, yard: bucketForYards(yards) };
  }

  /** The server's rules (models.score_prediction): 10 a right part, +10 when all three are right. */
  function scorePick(pick, actual) {
    const typeOK = pick.type === actual.type;
    const dirOK = pick.dir === actual.dir;
    const yardOK = pick.yard === actual.yard;      // a LOSS never equals SHORT / MEDIUM / LONG
    const parts = [typeOK, dirOK, yardOK].filter(Boolean).length;
    let points = (typeOK ? POINTS.type : 0) + (dirOK ? POINTS.direction : 0) + (yardOK ? POINTS.distance : 0);
    if (parts === 3) points += POINTS.bonus;
    return { points, typeOK, dirOK, yardOK, parts };
  }

  /** The chains follow the last gain: a first down (or a new series after 4th down), or the next down. */
  function advanceDowns() {
    if (G.lastYards === null) {
      G.down = 1;
      G.toGo = 10;
    } else if (G.lastYards >= G.toGo || G.down >= 4) {
      G.down = 1;
      G.toGo = 10;
    } else {
      G.down += 1;
      G.toGo = Math.min(99, G.toGo - G.lastYards);
    }
  }

  const playLabel = () => [`Practice play ${G.play}`, downDistance({ down: G.down, distance: G.toGo })].filter(Boolean).join(" · ");
  const hasFullPick = () => !!(G.pick.type && G.pick.dir && G.pick.yard);

  // ------------------------------------------------------------------ flow

  function show(name) {
    for (const view of $$("[data-p-view]")) view.hidden = view.dataset.pView !== name;
  }

  function stopTimers() {
    clearInterval(G.ticker);
    clearTimeout(G.pause);
    G.ticker = null;
    G.pause = null;
  }

  function resetGame() {
    stopTimers();
    Object.assign(G, { play: 0, down: 1, toGo: 10, lastYards: null, phase: "idle", score: 0, played: 0, perfect: 0 });
    G.pick = { type: null, dir: null, yard: null };
    paintStats();
    $("#p-confetti").replaceChildren();
  }

  function nextPlay() {
    stopTimers();
    $("#p-confetti").replaceChildren();
    G.play += 1;
    if (G.play > 1) advanceDowns();
    G.pick = { type: null, dir: null, yard: null };
    G.phase = "open";
    G.deadline = Date.now() + WINDOW_SECONDS * 1000;
    $("#p-open-kicker").textContent = playLabel();
    show("open");
    paintPicks();
    const ring = $("#p-ring");
    ring.style.strokeDasharray = String(RING);
    let lastShown = null;
    const tick = () => {
      const left = Math.max(0, (G.deadline - Date.now()) / 1000);
      const shown = Math.ceil(left);
      ring.style.strokeDashoffset = String(RING * (1 - left / WINDOW_SECONDS));
      if (shown !== lastShown) {
        lastShown = shown;
        $("#p-countdown-num").textContent = String(shown);
        $("#p-countdown").classList.toggle("hurry", left <= 7 && left > 3);
        $("#p-countdown").classList.toggle("critical", left <= 3);
      }
      if (left <= 0) snap();
    };
    tick();
    if (G.phase === "open") G.ticker = setInterval(tick, 100);
  }

  /** Lock the picks and run the play: when the timer ends, or when the player taps "Snap the ball". */
  function snap() {
    if (G.phase !== "open") return;
    stopTimers();
    G.phase = "locked";
    $("#p-locked-kicker").textContent = playLabel();
    pickChips($("#p-locked-pick"), hasFullPick() ? G.pick : null, null);
    show("locked");
    G.pause = setTimeout(resolve, LOCK_PAUSE_MS);
  }

  function resolve() {
    stopTimers();
    const actual = randomOutcome();
    // The pick only counts once all three parts are in, as in the live game.
    const scored = hasFullPick() ? scorePick(G.pick, actual) : null;
    if (scored) {
      G.score += scored.points;
      G.played += 1;
      if (scored.parts === 3) G.perfect += 1;
    }
    G.lastYards = actual.yards;
    G.phase = "result";
    paintStats();
    paintResult(actual, scored);
  }

  // ------------------------------------------------------------------ painting

  function paintStats() {
    $("#p-score").textContent = String(G.score);
    $("#p-played").textContent = String(G.played);
    $("#p-perfect").textContent = String(G.perfect);
  }

  function paintPicks() {
    const open = G.phase === "open";
    for (const [attr, key] of [["pType", "type"], ["pDir", "dir"], ["pYard", "yard"]]) {
      for (const button of $$(`[data-${attr.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}]`, modal)) {
        const on = button.dataset[attr] === G.pick[key];
        button.classList.toggle("selected", on);
        button.setAttribute("aria-pressed", String(on));
        button.disabled = !open;
      }
    }
    const { type, dir, yard } = G.pick;
    const missing = [!type && "Run or Pass", !dir && "a direction", !yard && "how far"].filter(Boolean);
    const status = $("#p-pick-status");
    if (!missing.length) {
      status.textContent = `✓ Ready: ${type} · ${dir} · ${yard}. Change it until the clock hits 0.`;
      status.className = "pick-status saved";
    } else {
      status.textContent = missing.length === 3 ? "Make your call: type, direction, distance" : `Now pick ${missing.join(" and ")}`;
      status.className = "pick-status";
    }
    const snapButton = $("#p-snap");
    snapButton.disabled = !open || !hasFullPick();
    snapButton.textContent = hasFullPick() ? "Snap the ball" : "Pick all three, then snap";
  }

  function pickChips(container, pick, scored) {
    container.replaceChildren();
    if (!pick) {
      container.append(el("span", { class: "chip" }, "No pick this play"));
      return;
    }
    const chip = (text, ok, what) =>
      el("span", {
        class: `chip ${scored ? (ok ? "good" : "bad") : ""}`,
        "aria-label": scored ? `${what} ${text}: ${ok ? "right" : "wrong"}` : null,
      }, scored ? `${text} ${ok ? "✓" : "✗"}` : text);
    container.append(
      el("span", { class: "chip" }, "Your pick"),
      chip(pick.type, scored && scored.typeOK, "Play type"),
      chip(pick.dir, scored && scored.dirOK, "Direction"),
      chip(pick.yard, scored && scored.yardOK, "Distance"),
    );
  }

  const LABELS = ["No points this time", "One of three", "Two of three", "Perfect call!"];

  function paintResult(actual, scored) {
    const view = $('[data-p-view="result"]');
    $("#p-result-kicker").textContent = `${playLabel()} — Result`;
    $("#p-reveal-type").textContent = actual.type;
    $("#p-reveal-dir").textContent = actual.dir;
    $("#p-reveal-yard").textContent = actual.yard;
    $("#p-reveal-yard-tile").classList.toggle("loss", actual.yard === "LOSS");
    $("#p-reveal-yards").textContent = yardsText(actual.yards);
    $("#p-reveal").setAttribute(
      "aria-label",
      `The play: ${titleCase(actual.type)} · ${titleCase(actual.dir)} · ${titleCase(actual.yard)} (${yardsText(actual.yards)})`,
    );

    const points = $("#p-points");
    points.textContent = scored ? `+${scored.points}` : "—";
    points.className = `points ${scored && scored.parts === 3 ? "exact" : scored && scored.points ? "some" : "zero"}`;
    $("#p-label").textContent = scored ? LABELS[scored.parts] : "You didn't pick this play";
    pickChips($("#p-result-pick"), scored ? G.pick : null, scored);

    show("result");
    view.classList.remove("animate");
    void view.offsetWidth;                       // restart the CSS animations
    view.classList.add("animate");
    if (scored && scored.parts === 3) confetti();
    $("#p-next").focus({ preventScroll: true });
  }

  function confetti() {
    const host = $("#p-confetti");
    const css = getComputedStyle(document.documentElement);
    const colors = ["--gold", "--accent", "--blue"].map((v) => css.getPropertyValue(v).trim()).filter(Boolean);
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

  // ------------------------------------------------------------------ open / close

  function openPractice(opener) {
    G.opener = opener || null;
    resetGame();
    modal.hidden = false;
    nextPlay();
    const first = $("[data-p-type]", modal);
    if (first) first.focus({ preventScroll: true });
  }

  function closePractice() {
    stopTimers();
    G.phase = "idle";
    modal.hidden = true;
    if (G.opener && G.opener.isConnected && G.opener.offsetParent !== null) G.opener.focus({ preventScroll: true });
  }

  function choose(key, value) {
    if (G.phase !== "open") return;
    G.pick[key] = value;
    if (navigator.vibrate) navigator.vibrate(8);
    paintPicks();
  }

  for (const button of $$("[data-practice-open]")) button.addEventListener("click", () => openPractice(button));
  for (const button of $$("[data-p-type]", modal)) button.addEventListener("click", () => choose("type", button.dataset.pType));
  for (const button of $$("[data-p-dir]", modal)) button.addEventListener("click", () => choose("dir", button.dataset.pDir));
  for (const button of $$("[data-p-yard]", modal)) button.addEventListener("click", () => choose("yard", button.dataset.pYard));
  $("#p-snap").addEventListener("click", snap);
  $("#p-next").addEventListener("click", nextPlay);
  $("#practice-done").addEventListener("click", closePractice);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !modal.hidden) closePractice();
  });
})();

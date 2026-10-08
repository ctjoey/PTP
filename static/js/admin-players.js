/* Pick the Play — host console: the message to the players, and the Players list (remove / block a name).
 *
 * admin.js creates it with PTPHost.create(ctx) and calls hostTools.render(state) for every admin_state.
 *   Message:  the `announce` admin action (empty text clears the banner); admin_state.announcement says what is showing.
 *   Players:  GET /api/admin/players[?q=] for the list, the `remove_player` admin action to remove (optionally blocking
 *             the name) and `unblock_name` to undo a block.
 * Everything is optional: with an older server (no `announcement` / `registered_players` in admin_state) the cards just
 * stay empty and the console works as before.
 *
 * Keyboard: admin.js turns bare keys (O, L, Enter ...) into console shortcuts whenever focus is on the page itself, so
 * whenever this file repaints or disables something that had focus it hands focus to a real control right away.
 */
"use strict";

const PTPHost = (() => {
  const { $, el, toast, api } = PTP;

  const MAX_MESSAGE = 200;
  const REFRESH_MS = 20000;   // while the Players list is open
  const SEARCH_DELAY_MS = 250;
  const SHOW_ROWS = 200;

  function create(ctx) {
    const S = {
      players: [], blocked: [], count: null, filter: "", asking: null, error: "",
      timer: null, search: null, loading: false, again: false, sending: false, removing: false, sig: "",
    };
    const text = $("#msg-text");
    const details = $("#players-details");

    // ---------------------------------------------------------------- message

    function paintMessage() {
      $("#msg-count").textContent = `${text.value.length} / ${MAX_MESSAGE}`;
      $("#msg-send").disabled = S.sending || !text.value.trim();
    }

    async function send() {
      const value = text.value.trim();
      if (!value || S.sending) return;
      S.sending = true;
      paintMessage();
      text.focus();                       // the Send button is about to disable itself: keep focus on a real control
      const ack = await ctx.act("announce", { text: value });
      S.sending = false;
      if (ack && ack.ok) {
        const n = ack.result.sent_to;
        toast(`Message sent to ${n} ${n === 1 ? "screen" : "screens"}.`, "success");
        text.value = "";
      }
      paintMessage();
      text.focus();
    }

    async function clearBanner() {
      text.focus();                       // Clear banner disables itself once the banner is gone
      const ack = await ctx.act("announce", { text: "" });
      if (ack && ack.ok) toast("Banner cleared.", "success");
      text.focus();
    }

    // ---------------------------------------------------------------- players

    async function refresh() {
      if (S.loading) {                    // one is already in flight: run again when it finishes, so nothing is missed
        S.again = true;
        return;
      }
      S.loading = true;
      try {
        const query = S.filter.trim();
        const data = await api(`/api/admin/players${query ? `?q=${encodeURIComponent(query)}` : ""}`, { adminKey: ctx.getKey() });
        S.players = data.players || [];
        S.blocked = data.blocked_names || [];
        S.count = data.count;                 // everyone signed up, whatever the search
        S.error = "";
        if (S.asking !== null && !S.players.some((p) => p.id === S.asking)) S.asking = null;
      } catch (err) {
        S.error = err.message || "Couldn't load the players.";
        if (err.status === 401) stopTimer();   // signed out: stop asking
      } finally {
        S.loading = false;
      }
      paintPlayers();
      if (S.again) {
        S.again = false;
        refresh();
      }
    }

    async function remove(player, block) {
      if (S.removing) return;
      S.removing = true;
      S.asking = null;
      paintPlayers(true);
      $("#players-search").focus();       // the row (and its buttons) is going away
      const ack = await ctx.act("remove_player", { user_id: player.id, block });
      S.removing = false;
      if (ack && ack.ok) toast(`Removed ${player.username}${block ? " and blocked the name" : ""}.`, "success", 4500);
      await refresh();
    }

    async function unblock(name) {
      const ack = await ctx.act("unblock_name", { name });
      if (ack && ack.ok) toast(`Unblocked ${name}.`, "success");
      await refresh();
    }

    function focusRow(id, selector) {
      const target = document.querySelector(`#players-list .player-row[data-id="${id}"] ${selector}`);
      if (target) target.focus();
    }

    function ask(player) {
      S.asking = player.id;
      paintPlayers(true);
      focusRow(player.id, ".js-cancel");  // the safe choice is the one under the cursor of a keyboard user
    }

    function cancel(player) {
      S.asking = null;
      paintPlayers(true);
      focusRow(player.id, ".js-remove");
    }

    function confirmRow(player) {
      return el("div", { class: "player-confirm", role: "group", "aria-label": `Remove ${player.username}?` },
        el("span", { class: "small" }, "Remove for good?"),
        el("button", { class: "btn btn-sm", type: "button", onclick: () => remove(player, true) }, "Remove and block name"),
        el("button", { class: "btn btn-sm", type: "button", onclick: () => remove(player, false) }, "Remove only"),
        el("button", { class: "btn btn-sm btn-ghost js-cancel", type: "button", onclick: () => cancel(player) }, "Cancel"),
      );
    }

    function playerRow(player) {
      const asking = S.asking === player.id;
      const meta = [
        `${player.picks} ${player.picks === 1 ? "pick" : "picks"}`,
        `${player.game_score} game pts`,
        `${player.total_score} season`,
      ].join(" · ");
      return el("li", { class: `player-row${asking ? " asking" : ""}`, dataset: { id: String(player.id) } },
        el("div", { class: "player-info" },
          el("b", { class: "player-name" }, player.username,
            player.online ? el("span", { class: "online-dot", title: "Online now", role: "img", "aria-label": "online now" }) : null),
          el("span", { class: "player-meta" }, meta)),
        asking
          ? confirmRow(player)
          : el("button", {
            class: "btn btn-sm js-remove", type: "button", "aria-label": `Remove ${player.username}`,
            onclick: () => ask(player),
          }, "Remove"),
      );
    }

    /** Repaint the list (skipped when nothing changed, so a refresh never swallows a click in progress). */
    function paintPlayers(force = false) {
      const sig = JSON.stringify([S.players, S.blocked, S.error, S.filter, S.asking]);
      if (!force && sig === S.sig) return;
      S.sig = sig;
      const list = $("#players-list");
      const filter = S.filter.trim().toLowerCase();
      const rows = S.players.filter((p) => !filter || p.username.toLowerCase().includes(filter));
      list.replaceChildren();
      if (S.error) {
        list.append(el("li", { class: "empty" }, S.error));
      } else if (!rows.length) {
        list.append(el("li", { class: "empty" }, filter ? "No one by that name." : "No one has signed up yet."));
      } else {
        for (const player of rows.slice(0, SHOW_ROWS)) list.append(playerRow(player));
        if (rows.length > SHOW_ROWS) list.append(el("li", { class: "empty" }, `Showing ${SHOW_ROWS} of ${rows.length}. Type a name to narrow it down.`));
      }
      const blocked = $("#players-blocked");
      blocked.replaceChildren();
      if (S.blocked.length) {
        blocked.append(el("span", { class: "muted" }, "Blocked names:"));
        for (const name of S.blocked) {
          blocked.append(el("span", { class: "blocked-chip" }, name,
            el("button", {
              class: "link-btn", type: "button", "aria-label": `Unblock ${name}`, onclick: () => unblock(name),
            }, "Unblock")));
        }
      }
    }

    function stopTimer() {
      clearInterval(S.timer);
      S.timer = null;
    }

    function onToggle() {
      stopTimer();
      if (!details.open) return;
      refresh();
      S.timer = setInterval(refresh, REFRESH_MS);
    }

    // ---------------------------------------------------------------- wiring

    $("#msg-send").addEventListener("click", send);
    $("#msg-clear").addEventListener("click", clearBanner);
    text.addEventListener("input", paintMessage);
    text.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        send();
      }
    });
    for (const button of document.querySelectorAll("[data-msg]")) {
      button.addEventListener("click", () => {
        text.value = button.dataset.msg;
        paintMessage();
        text.focus();
      });
    }
    $("#players-search").addEventListener("input", (ev) => {
      S.filter = ev.target.value;
      S.asking = null;
      paintPlayers(true);                 // narrow what we have right away ...
      clearTimeout(S.search);
      S.search = setTimeout(refresh, SEARCH_DELAY_MS);   // ... and ask the server (the list is capped)
    });
    $("#players-refresh").addEventListener("click", refresh);
    details.addEventListener("toggle", onToggle);
    paintMessage();

    /** Called for every admin_state. */
    function render(st) {
      const banner = st.announcement;
      $("#msg-status").textContent = banner ? `Showing now: “${banner.text}”` : "No banner is showing.";
      $("#msg-clear").disabled = !banner;
      const registered = st.registered_players;
      $("#players-count").textContent = typeof registered === "number" ? `(${registered})` : "";
      // Someone signed up or was removed (here or elsewhere): bring an open list up to date.
      if (details.open && typeof registered === "number" && S.count !== null && registered !== S.count) refresh();
    }

    return { render };
  }

  return { create };
})();

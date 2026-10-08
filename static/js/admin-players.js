/* Pick the Play — host console: the message to the players, and the Players list (remove / block a name).
 *
 * admin.js creates it with PTPHost.create(ctx) and calls hostTools.render(state) for every admin_state.
 *   Message:  the `announce` admin action (empty text clears the banner); admin_state.announcement says what is showing.
 *   Players:  GET /api/admin/players for the list, the `remove_player` admin action to remove (optionally blocking the name).
 * Everything is optional: with an older server (no `announcement` / `registered_players` in admin_state) the cards just
 * stay empty and the console works as before.
 */
"use strict";

const PTPHost = (() => {
  const { $, el, toast, api } = PTP;

  const MAX_MESSAGE = 200;
  const REFRESH_MS = 20000;   // while the Players list is open
  const SHOW_ROWS = 200;

  function create(ctx) {
    const S = { players: [], blocked: [], count: null, filter: "", asking: null, error: "", timer: null, loading: false };
    const text = $("#msg-text");
    const details = $("#players-details");

    // ---------------------------------------------------------------- message

    function paintMessage() {
      $("#msg-count").textContent = `${text.value.length} / ${MAX_MESSAGE}`;
      $("#msg-send").disabled = !text.value.trim();
    }

    async function send() {
      const value = text.value.trim();
      if (!value) return;
      const ack = await ctx.act("announce", { text: value });
      if (ack && ack.ok) {
        const n = ack.result.sent_to;
        toast(`Message sent to ${n} ${n === 1 ? "screen" : "screens"}.`, "success");
        text.value = "";
        paintMessage();
      }
    }

    async function clearBanner() {
      const ack = await ctx.act("announce", { text: "" });
      if (ack && ack.ok) toast("Banner cleared.", "success");
    }

    // ---------------------------------------------------------------- players

    async function refresh() {
      if (S.loading) return;
      S.loading = true;
      try {
        const data = await api("/api/admin/players", { adminKey: ctx.getKey() });
        S.players = data.players || [];
        S.blocked = data.blocked_names || [];
        S.count = data.count;
        S.error = "";
        if (S.asking !== null && !S.players.some((p) => p.id === S.asking)) S.asking = null;
      } catch (err) {
        S.error = err.message || "Couldn't load the players.";
      } finally {
        S.loading = false;
      }
      paintPlayers();
    }

    async function remove(player, block) {
      const ack = await ctx.act("remove_player", { user_id: player.id, block });
      S.asking = null;
      if (ack && ack.ok) toast(`Removed ${player.username}${block ? " and blocked the name" : ""}.`, "success", 4500);
      await refresh();
    }

    function confirmRow(player) {
      const label = `Remove ${player.username}?`;
      return el("div", { class: "player-confirm", role: "group", "aria-label": label },
        el("span", { class: "small" }, "Remove for good?"),
        el("button", { class: "btn btn-sm btn-warn", type: "button", onclick: () => remove(player, true) }, "Remove and block name"),
        el("button", { class: "btn btn-sm", type: "button", onclick: () => remove(player, false) }, "Remove only"),
        el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: () => { S.asking = null; paintPlayers(); } }, "Cancel"),
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
            class: "btn btn-sm", type: "button", "aria-label": `Remove ${player.username}`,
            onclick: () => { S.asking = player.id; paintPlayers(); },
          }, "Remove"),
      );
    }

    function paintPlayers() {
      const list = $("#players-list");
      const filter = S.filter.trim().toLowerCase();
      const rows = S.players.filter((p) => !filter || p.username.toLowerCase().includes(filter));
      list.replaceChildren();
      if (S.error) {
        list.append(el("li", { class: "empty" }, S.error));
      } else if (!rows.length) {
        list.append(el("li", { class: "empty" }, S.players.length ? "No one by that name." : "No one has signed up yet."));
      } else {
        for (const player of rows.slice(0, SHOW_ROWS)) list.append(playerRow(player));
        if (rows.length > SHOW_ROWS) list.append(el("li", { class: "empty" }, `Showing ${SHOW_ROWS} of ${rows.length}. Type a name to narrow it down.`));
      }
      $("#players-blocked").textContent = S.blocked.length ? `Blocked names: ${S.blocked.join(", ")}` : "";
    }

    function onToggle() {
      clearInterval(S.timer);
      S.timer = null;
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
      paintPlayers();
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

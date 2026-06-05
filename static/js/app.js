/*
 * KARMA web UI -- application shell.
 *
 * Holds the view registry and tab navigation. Each view module calls
 * KARMA.registerView({id, label, mount}) at load time; this file renders a
 * tab per view and mounts the active one into #view. The active tab is kept
 * in the URL hash so a view survives a reload. A cluster-status banner polls
 * /api/services so the operator is warned when no cluster is reachable.
 */
(function () {
  "use strict";

  const KARMA = (window.KARMA = window.KARMA || {});
  const { el, clear } = KARMA;

  KARMA.views = KARMA.views || [];
  KARMA.registerView = function registerView(view) {
    KARMA.views.push(view);
  };

  let activeId = null;

  function renderNav() {
    const nav = document.getElementById("nav");
    clear(nav);
    for (const view of KARMA.views) {
      nav.appendChild(
        el("button", {
          class: "tab" + (view.id === activeId ? " active" : ""),
          onClick: () => activate(view.id),
        }, view.label)
      );
    }
  }

  function activate(id) {
    activeId = id;
    if (location.hash !== "#" + id) location.hash = id;
    renderNav();
    const container = clear(document.getElementById("view"));
    const view = KARMA.views.find((v) => v.id === id);
    if (!view) {
      container.appendChild(el("p", { class: "muted" }, "No such view."));
      return;
    }
    try {
      view.mount(container);
    } catch (e) {
      container.appendChild(el("div", { class: "error-box" }, "View error: " + e.message));
    }
  }

  KARMA.activate = activate;

  async function refreshClusterBanner() {
    const banner = document.getElementById("cluster-banner");
    if (!banner) return;
    try {
      const data = await KARMA.api.get("/api/services");
      const cluster = (data && data.cluster) || {};
      const ok = cluster.status === "ok";
      banner.className = "banner " + (ok ? "ok" : "warn");
      banner.textContent = ok
        ? "Cluster reachable"
        : "Cluster: " + (cluster.status || "unknown") +
          (cluster.detail ? " — " + cluster.detail : "");
    } catch (e) {
      banner.className = "banner warn";
      banner.textContent = "Backend unreachable: " + e.message;
    }
  }

  function boot() {
    renderNav();
    const fromHash = (location.hash || "").replace(/^#/, "");
    const initial =
      KARMA.views.find((v) => v.id === fromHash) ? fromHash :
      (KARMA.views[0] && KARMA.views[0].id);
    if (initial) activate(initial);
    refreshClusterBanner();
    setInterval(refreshClusterBanner, 15000);
    window.addEventListener("hashchange", () => {
      const id = (location.hash || "").replace(/^#/, "");
      if (id && id !== activeId) activate(id);
    });
  }

  window.addEventListener("DOMContentLoaded", boot);
})();

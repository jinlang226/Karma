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
      if (view.hidden) continue;   // e.g. home -- reachable via the brand, not a tab
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
    // Restart the enter animation on every switch (including the brand -> home),
    // so navigating always fades the new view in rather than snapping it in.
    container.style.animation = "none";
    void container.offsetWidth;   // force reflow so the animation can replay
    container.style.animation = "";
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

  // --- Toast / status notifications (bottom-right) -------------------------
  function ensureToastHost() {
    let host = document.getElementById("toasts");
    if (!host) {
      host = el("div", { id: "toasts" });
      document.body.appendChild(host);
    }
    return host;
  }

  // KARMA.toast(message, type) -- type: "error" | "success" | "info".
  // Errors persist longer; status toasts auto-dismiss. All have a close ✕.
  KARMA.toast = function toast(message, type) {
    type = type || "info";
    const host = ensureToastHost();
    const node = el("div", { class: "toast " + type });
    node.appendChild(el("span", { class: "toast-msg" }, String(message || "")));
    const close = el("button", {
      class: "toast-close", title: "Dismiss", onClick: () => dismiss(),
    }, "✕");
    node.appendChild(close);
    host.appendChild(node);
    const ttl = type === "error" ? 12000 : type === "success" ? 5000 : 7000;
    let timer = setTimeout(dismiss, ttl);
    function dismiss() {
      clearTimeout(timer);
      timer = null;
      node.classList.add("leaving");
      setTimeout(() => node.remove(), 200);
    }
    return node;
  };
  KARMA.toastError = (e) => KARMA.toast(e && e.message ? e.message : String(e), "error");

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
    const brand = document.querySelector("#topbar .brand");
    if (brand) brand.addEventListener("click", () => activate("home"));
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

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
          "aria-current": view.id === activeId ? "page" : null,
          onClick: () => { KARMA.clearHistory(); activate(view.id); },
        }, view.label)
      );
    }
  }

  function activate(id) {
    activeId = id;
    if (location.hash !== "#" + id) location.hash = id;
    // Switching views: close any live SSE stream so it stops firing into the
    // page we are leaving (stale toasts / appends to detached nodes).
    if (KARMA.api && KARMA.api.closeAllStreams) KARMA.api.closeAllStreams();
    renderNav();
    if (KARMA.setBreadcrumb) KARMA.setBreadcrumb(null);   // each view sets its own
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

  // --- Cross-view navigation history --------------------------------------
  // A stack of "restore thunks", one per page left behind during a cross-view
  // jump (e.g. Results detail -> a Cases sub-page). The back arrow pops it so
  // it returns to the LAST viewed page, not just the current view's parent.
  // Each returnable page sets KARMA.currentLocation to a thunk that re-renders
  // itself; navigateTo() captures that before sending you elsewhere.
  const navStack = [];
  KARMA.currentLocation = null;
  KARMA.navigateTo = function (navFn) {
    if (KARMA.currentLocation) navStack.push(KARMA.currentLocation);
    navFn();
  };
  KARMA.navBack = function () {
    const fn = navStack.pop();
    if (fn) { fn(); return true; }
    return false;
  };
  KARMA.clearHistory = function () { navStack.length = 0; };

  // Breadcrumb beside the brand. spec = null (clear) or
  // { back: fn|null, crumbs: [{label, onClick|null}] }. Crumbs are the
  // clickable ancestors of the current page; the current page's own name
  // stays in the page heading. The back arrow prefers the cross-view history
  // stack (last viewed page) and falls back to the view's own parent.
  KARMA.setBreadcrumb = function (spec) {
    const host = document.getElementById("breadcrumb");
    if (!host) return;
    clear(host);
    if (!spec) return;
    if (spec.back || navStack.length) {
      host.appendChild(el("button", {
        class: "crumb-back", title: "Back", "aria-label": "Back",
        onClick: () => { if (!KARMA.navBack() && spec.back) spec.back(); },
      }, "←"));
    }
    (spec.crumbs || []).forEach((c, i) => {
      if (i > 0) host.appendChild(el("span", { class: "crumb-sep" }, "/"));
      host.appendChild(c.onClick
        ? el("span", { class: "crumb-link", onClick: c.onClick }, c.label)
        : el("span", { class: "crumb-current" }, c.label));
    });
  };

  // --- Toast / status notifications (bottom-right) -------------------------
  function ensureToastHost() {
    let host = document.getElementById("toasts");
    if (!host) {
      // role=status + aria-live so screen readers announce toasts as they appear.
      host = el("div", { id: "toasts", role: "status", "aria-live": "polite" });
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
      class: "toast-close", title: "Dismiss", "aria-label": "Dismiss", onClick: () => dismiss(),
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

  // --- Stage failure detail -----------------------------------------------
  function logBlock(title, text) {
    return el("div", { class: "log-block" },
      el("div", { class: "log-block-title" }, title),
      el("pre", { class: "log" }, text));
  }

  // A failure-detail block for one stage: status + error inline, plus a lazy
  // "view logs" expander that pulls the triggering precondition command, the
  // oracle output, and the agent log from /api/run/<id>/stages/<stage_id>.
  // `stage` is the stage object from a stage_complete event.
  KARMA.stageDetail = function stageDetail(runId, stage) {
    const sid = stage.stage_id || "?";
    const st = KARMA.labels && KARMA.labels.status
      ? KARMA.labels.status(stage.status) : { text: stage.status, cls: "bad" };
    const wrap = el("div", { class: "stage-detail " + (st.cls || "bad") });
    wrap.appendChild(el("div", { class: "stage-detail-head" },
      el("strong", {}, KARMA.humanize(sid)),
      el("span", { class: "badge " + (st.cls || "bad") }, st.text || stage.status || "?"),
      stage.oracle_verdict ? el("span", { class: "muted" }, "oracle: " + stage.oracle_verdict) : null));
    if (stage.error) wrap.appendChild(el("div", { class: "stage-error" }, stage.error));

    const body = el("div", { class: "stage-logs", style: "display:none" });
    let loaded = false;
    const toggle = el("button", { class: "btn secondary small" }, "▸ view logs");
    toggle.addEventListener("click", async () => {
      const open = body.style.display === "none";
      body.style.display = open ? "" : "none";
      toggle.textContent = open ? "▾ hide logs" : "▸ view logs";
      if (!open || loaded) return;
      loaded = true;
      body.appendChild(el("span", { class: "muted" }, "Loading…"));
      try {
        const d = await KARMA.api.get(`/api/run/${runId}/stages/${sid}`);
        clear(body);
        if (d.precondition_log)
          body.appendChild(logBlock("Setup (the command that triggered the failure)", d.precondition_log));
        if (d.oracle) {
          const o = d.oracle;
          body.appendChild(logBlock("Oracle" + (o.verdict ? ` (${o.verdict})` : ""),
            o.output || JSON.stringify(o, null, 2)));
        }
        if (d.agent_log) body.appendChild(logBlock("Agent log", d.agent_log));
        if (!d.precondition_log && !d.oracle && !d.agent_log)
          body.appendChild(el("p", { class: "muted" }, "No logs captured for this stage."));
      } catch (e) {
        clear(body);
        body.appendChild(el("div", { class: "error-box" }, e.message));
      }
    });
    wrap.appendChild(toggle);
    wrap.appendChild(body);
    return wrap;
  };

  // A read-only panel of a workflow's stages (service/case/param overrides).
  // Shared by the Workflow detail view and the Results run detail.
  // Palette for adversary injections (distinct, theme-friendly).
  const ADV_COLORS = ["#c6632d", "#2f6f6d", "#7b5ea7", "#b08900", "#a23b5e", "#3a7d44"];

  KARMA.workflowStagesPanel = function (wf, title, onStageClick) {
    const panel = el("div", { class: "panel" });
    panel.appendChild(el("h3", {}, title || "Workflow stages"));
    const stages = wf.stages || [];
    const idx = {};
    stages.forEach((s, i) => { idx[s.id] = i; });
    const injections = (wf.adversary || []).map((a, i) => ({
      scenario: a.scenario, inject: a.inject_at_stage, lift: a.lift_at_stage,
      color: ADV_COLORS[i % ADV_COLORS.length],
      from: idx[a.inject_at_stage],
      to: (idx[a.lift_at_stage] == null ? idx[a.inject_at_stage] : idx[a.lift_at_stage]),
    })).filter((a) => a.from != null);
    // Scrollable so a many-stage workflow (e.g. 30 stages) stays a bounded block.
    const list = el("div", { class: "stage-scroll" });
    stages.forEach((s, i) => {
      const cover = injections.filter((a) => i >= a.from && i <= a.to);
      const row = el("div", { class: "builder-row" + (cover.length ? " stage-injected" : "") + (onStageClick ? " clickable" : "") });
      // Tint the WHOLE box with the covering injection color(s).
      if (cover.length === 1) {
        row.style.background = cover[0].color + "1f";
        row.style.borderColor = cover[0].color + "77";
      } else if (cover.length > 1) {
        row.style.background = `linear-gradient(135deg, ${cover.map((a) => a.color + "2b").join(", ")})`;
      }
      if (onStageClick && s.service && s.case_name) row.addEventListener("click", () => KARMA.navigateTo(() => onStageClick(s)));
      // Header: stage id + inject/lift marks INSIDE the box.
      const head = el("div", { class: "builder-row-head" }, el("span", {}, KARMA.humanize(s.id)));
      injections.filter((a) => a.from === i).forEach((a) => head.appendChild(
        el("span", { class: "adv-badge", style: `color:${a.color};border-color:${a.color}` }, "⚠ " + KARMA.labels.scenario(a.scenario))));
      injections.filter((a) => a.to === i).forEach((a) => head.appendChild(
        el("span", { class: "adv-badge lift", style: `color:${a.color}` }, "↑ lifted")));
      row.appendChild(head);
      row.appendChild(el("div", { class: "kv" }, el("span", { class: "k" }, "Service"), el("span", {}, KARMA.labels.service(s.service))));
      row.appendChild(el("div", { class: "kv" }, el("span", { class: "k" }, "Case"), el("span", {}, KARMA.labels.case(s.case_name))));
      for (const [k, v] of Object.entries(s.param_overrides || {})) {
        row.appendChild(el("div", { class: "kv" }, el("span", { class: "k" }, KARMA.labels.case(k)), el("span", {}, String(v))));
      }
      list.appendChild(row);
    });
    if (!stages.length) list.appendChild(el("p", { class: "muted" }, "No stages."));
    panel.appendChild(list);
    // Legend of the injections (scenario + span).
    if (injections.length) {
      const legend = el("div", { class: "adv-legend" });
      injections.forEach((a) => legend.appendChild(el("span", {
        class: "adv-legend-item clickable",
        onClick: () => KARMA.navigateTo(() => KARMA.showScenario(a.scenario)),
      },
        el("span", { class: "adv-swatch", style: `background:${a.color}` }),
        `${KARMA.labels.scenario(a.scenario)} (${a.inject} → ${a.lift})`)));
      panel.appendChild(legend);
    }
    return panel;
  };

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
    if (brand) brand.addEventListener("click", () => { KARMA.clearHistory(); activate("home"); });
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

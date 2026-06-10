/*
 * KARMA web UI -- Results view (the central hub for runs).
 *
 * Two subviews:
 *   - Runs: a live + historical list of every run (newest first, auto-refresh
 *     while any run is active). Click a run to open its detail.
 *   - Batches: cross-run judge batches (carried over from the old Judge view).
 *
 * A run's detail shows its config, per-stage status with on-demand failure logs
 * (prompt / precondition command / oracle / agent log via KARMA.stageDetail),
 * reconnects to the live SSE stream while running (the hub replays history, so
 * it resumes even after navigating away), and judges the run in place.
 */
(function () {
  "use strict";
  const KARMA = window.KARMA;
  const { el, clear, api } = KARMA;

  let root;
  let sub = "runs";        // "runs" | "batches"
  let refreshTimer = null;

  const TERMINAL = ["complete", "failed", "error", "passed", "cancelled"];
  function isTerminal(s) { return TERMINAL.includes(s); }

  function errBox(e) {
    const m = e.message || String(e);
    KARMA.toast(m, "error");
    return el("div", { class: "error-box" }, m);
  }
  function scoreCell(v) {
    if (v == null) return el("span", { class: "muted" }, "—");
    const cls = v >= 0.8 ? "ok" : v >= 0.5 ? "warn" : "bad";
    return el("span", { class: "badge " + cls }, v.toFixed(3));
  }
  function statusBadge(id) {
    if (!id) return el("span", { class: "muted" }, "—");
    const st = KARMA.labels.status(id);
    return el("span", { class: "badge " + st.cls }, st.text);
  }

  function mount(container) { root = container; sub = "runs"; render(); }

  function stopTimers() { if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; } }

  function subtabs() {
    return el("div", { class: "subtabs" },
      el("button", { class: "tab" + (sub === "runs" ? " active" : ""), onClick: () => { sub = "runs"; render(); } }, "Runs"),
      el("button", { class: "tab" + (sub === "batches" ? " active" : ""), onClick: () => { sub = "batches"; render(); } }, "Batches"));
  }

  function render() {
    stopTimers();
    clear(root);
    KARMA.setBreadcrumb(null);
    root.appendChild(el("h2", {}, "Results"));
    root.appendChild(el("p", { class: "field-help" },
      "Every run, live and historical. Click a run for its config, per-stage " +
      "status and failure logs, and to judge it."));
    root.appendChild(subtabs());
    if (sub === "batches") { renderBatches(); return; }
    const panel = el("div", { class: "panel" });
    root.appendChild(panel);
    loadRuns(panel);
  }

  async function loadRuns(panel) {
    let runs;
    try { runs = await api.get("/api/runs"); }
    catch (e) { clear(panel); panel.appendChild(errBox(e)); return; }
    clear(panel);
    const tbl = el("table", {}, el("thead", {}, el("tr", {},
      el("th", {}, "Run"), el("th", {}, "Status"), el("th", {}, "Stages"),
      el("th", {}, "Agent"), el("th", {}, "Score"))));
    const body = el("tbody", {});
    for (const r of runs) {
      const total = r.stage_count != null ? r.stage_count : ((r.passed || 0) + (r.failed || 0));
      const prog = total ? `${r.passed || 0}/${total}` : "—";
      const agent = r.agent ? KARMA.labels.agent(r.agent) : el("span", { class: "muted" }, "none");
      body.appendChild(el("tr", { class: "clickable", onClick: () => renderDetail(r.run_id) },
        el("td", {}, el("span", { class: "run-id" }, r.run_id)),
        el("td", {}, statusBadge(r.status)),
        el("td", {}, prog),
        el("td", {}, agent),
        el("td", {}, scoreCell(r.judge_score))));
    }
    if (!runs.length) body.appendChild(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "No runs yet.")));
    tbl.appendChild(body);
    panel.appendChild(tbl);
    // Auto-refresh while any run is still active so progress updates in place.
    if (runs.some((r) => !isTerminal(r.status))) {
      refreshTimer = setTimeout(() => {
        if (sub === "runs" && document.body.contains(panel)) loadRuns(panel);
      }, 3000);
    }
  }

  // --- Run detail -----------------------------------------------------------
  async function renderDetail(runId) {
    stopTimers();
    clear(root);
    KARMA.setBreadcrumb({ back: render, crumbs: [{ label: "Results", onClick: render }, { label: runId }] });
    root.appendChild(el("h2", {}, runId));

    let d;
    try { d = await api.get(`/api/run/${runId}`); }
    catch (e) { root.appendChild(errBox(e)); return; }
    const cfg = d.config || {};

    const badges = el("div", { class: "toolbar" });
    badges.appendChild(statusBadge(d.status));
    badges.appendChild(el("span", { class: "badge" },
      cfg.agent ? "agent: " + KARMA.labels.agent(cfg.agent) : "no agent"));
    if (cfg.sandbox) badges.appendChild(el("span", { class: "badge" }, cfg.sandbox));
    if (d.duration_sec) badges.appendChild(el("span", { class: "muted" }, Math.round(d.duration_sec) + "s"));
    root.appendChild(badges);

    if (cfg.params && Object.keys(cfg.params).length) {
      const p = el("div", { class: "panel" });
      p.appendChild(el("h3", {}, "Config"));
      for (const [k, v] of Object.entries(cfg.params)) {
        p.appendChild(el("div", { class: "kv" }, el("span", { class: "k" }, k), el("span", {}, String(v))));
      }
      root.appendChild(p);
    }

    // Judge (terminal) or Cancel (running), with an inline judge log.
    const judgeLog = el("pre", { class: "log", style: "display:none" });
    const actions = el("div", { class: "toolbar" });
    if (isTerminal(d.status)) {
      actions.appendChild(el("button", { class: "btn", onClick: () => startJudge("run", runId, false, judgeLog) }, "Judge"));
      actions.appendChild(el("button", { class: "btn secondary", onClick: () => startJudge("run", runId, true, judgeLog) }, "Dry run"));
    } else {
      actions.appendChild(el("button", { class: "btn secondary", onClick: () => {
        api.post(`/api/run/${runId}/cancel`).catch(() => {});
      } }, "Cancel"));
    }
    root.appendChild(actions);
    root.appendChild(judgeLog);

    const stagesPanel = el("div", { class: "panel" });
    stagesPanel.appendChild(el("h3", {}, "Stages"));
    const host = el("div", {});
    stagesPanel.appendChild(host);
    root.appendChild(stagesPanel);

    const byId = {};
    (d.stages || []).forEach((s) => { if (s.stage_id) byId[s.stage_id] = s; });
    function renderStages() {
      clear(host);
      const list = Object.values(byId);
      if (!list.length) { host.appendChild(el("p", { class: "muted" }, "No stages yet.")); return; }
      for (const s of list) host.appendChild(KARMA.stageDetail(runId, s));
    }
    renderStages();

    // Reconnect to the live stream while running -- the hub replays buffered
    // history, so this resumes even after navigating away and back.
    if (!isTerminal(d.status)) {
      const live = el("pre", { class: "log" }, "Streaming…\n");
      stagesPanel.appendChild(el("h3", {}, "Live"));
      stagesPanel.appendChild(live);
      api.stream(`/api/run/${runId}/stream`, {
        statusPath: `/api/run/${runId}/status`,
        onEvent: (ev) => {
          if (ev.type === "stage_complete") {
            const s = ev.stage || {};
            live.textContent += `stage ${s.stage_id}: ${s.status} (oracle=${s.oracle_verdict})\n`;
            if (s.stage_id) { byId[s.stage_id] = s; renderStages(); }
          } else if (ev.type === "run_complete") {
            live.textContent += `run complete: ${ev.status}\n`;
            renderDetail(runId);   // reload to show final state + judge buttons
          }
          live.scrollTop = live.scrollHeight;
        },
        onDone: () => {},
      });
    }
  }

  // --- Judge (job + stream) -- reused from the old Judge view ----------------
  async function startJudge(targetType, targetPath, dryRun, log) {
    log.style.display = "";
    log.textContent = `${dryRun ? "Dry-" : ""}judging ${targetPath}\n`;
    try {
      const { job_id } = await api.post("/api/judge/start", {
        target_type: targetType, target_path: targetPath, dry_run: dryRun,
      });
      log.textContent += "job " + job_id + "\n";
      api.stream(`/api/judge/jobs/${job_id}/stream`, {
        statusPath: `/api/judge/jobs/${job_id}`,
        onEvent: (ev) => {
          if (ev.type === "judge_progress") {
            const where = ev.stage_id ? `${ev.run_id}/${ev.stage_id}` : ev.run_id;
            log.textContent += `  ${where}: verdict=${ev.verdict ?? "-"} score=${ev.score ?? "-"}\n`;
          } else if (ev.type === "judge_complete") {
            log.textContent += `judge ${ev.status}\n`;
            KARMA.toast("Judge " + (ev.status || "complete"), ev.status === "error" ? "error" : "success");
          }
          log.scrollTop = log.scrollHeight;
        },
        onDone: () => { log.textContent += "— judge stream ended —\n"; },
      });
    } catch (e) { log.textContent += "Error: " + e.message + "\n"; KARMA.toastError(e); }
  }

  // --- Batches (cross-run judge) -- reused from the old Judge view -----------
  async function renderBatches() {
    const panel = el("div", { class: "panel" });
    root.appendChild(panel);
    const log = el("pre", { class: "log", style: "display:none" });
    try {
      const batches = await api.get("/api/judge/batches");
      const tbl = el("table", {}, el("thead", {}, el("tr", {},
        el("th", {}, "Batch"), el("th", {}, "Runs"), el("th", {}, "Judged"),
        el("th", {}, "Avg score"), el("th", {}, ""))));
      const body = el("tbody", {});
      for (const b of batches) {
        body.appendChild(el("tr", {},
          el("td", {}, b.name),
          el("td", {}, String(b.run_count)),
          el("td", {}, String(b.judged_count)),
          el("td", {}, scoreCell(b.average_final_score)),
          el("td", {}, el("span", { class: "toolbar", style: "margin:0" },
            el("button", { class: "btn", onClick: () => startJudge("batch", b.batch_dir, false, log) }, "Judge"),
            el("button", { class: "btn secondary", onClick: () => startJudge("batch", b.batch_dir, true, log) }, "Dry run")))));
      }
      if (!batches.length) body.appendChild(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "No batches found.")));
      tbl.appendChild(body);
      panel.appendChild(tbl);
      panel.appendChild(log);
    } catch (e) { panel.appendChild(errBox(e)); }
  }

  KARMA.registerView({ id: "results", label: "Results", mount });
})();

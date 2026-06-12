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
  let pendingRun = null;   // set by KARMA.showRun to deep-link a run detail

  // Cross-view deep link: open a specific run's detail (used by the back stack
  // so returning from a Cases sub-page lands on the exact run, not the list).
  KARMA.showRun = function (runId) { pendingRun = runId; KARMA.activate("results"); };

  // Sort key: the YYYYMMDD_HHMMSS stamp in the run_id, as a comparable string.
  // Runs without a stamp sort last.
  function runSortKey(r) {
    const m = String((r && r.run_id) || "").match(/(\d{8})_(\d{6})/);
    return m ? m[1] + m[2] : "";
  }

  const TERMINAL = ["complete", "failed", "error", "passed", "cancelled", "interrupted"];
  function isTerminal(s) { return TERMINAL.includes(s); }

  function errBox(e) {
    const m = e.message || String(e);
    KARMA.toast(m, "error");
    return el("div", { class: "error-box" }, m);
  }
  function scoreCell(v) {
    if (v == null) return el("span", { class: "muted" }, "—");
    // Scores are 0-100 (0.1 precision); tolerate any legacy 0-1 values until re-judged.
    const s = v <= 1 ? v * 100 : v;
    const cls = s >= 80 ? "ok" : s >= 50 ? "warn" : "bad";
    return el("span", { class: "badge " + cls }, s.toFixed(1));
  }
  function statusBadge(id) {
    if (!id) return el("span", { class: "muted" }, "—");
    const st = KARMA.labels.status(id);
    return el("span", { class: "badge " + st.cls }, st.text);
  }

  function mount(container) {
    root = container;
    sub = "runs";
    if (pendingRun) { const id = pendingRun; pendingRun = null; renderDetail(id); }
    else render();
  }

  function stopTimers() { if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; } }

  function subtabs() {
    return el("div", { class: "subtabs" },
      el("button", { class: "tab" + (sub === "runs" ? " active" : ""), onClick: () => { sub = "runs"; render(); } }, "Runs"),
      el("button", { class: "tab" + (sub === "batches" ? " active" : ""), onClick: () => { sub = "batches"; render(); } }, "Batches"));
  }

  function render() {
    stopTimers();
    clear(root);
    // The list is a root page: reset any cross-view back history and record it
    // as the current location so a later jump returns here.
    KARMA.clearHistory();
    KARMA.currentLocation = () => KARMA.activate("results");
    KARMA.setBreadcrumb(null);
    root.appendChild(el("h2", {}, "Results"));
    root.appendChild(el("p", { class: "field-help" },
      "Every run, live and historical. Click a run for its config, per-stage " +
      "status and failure logs, and to judge it."));
    root.appendChild(subtabs());
    if (sub === "batches") { renderBatches(); return; }
    // Transparent host with a muted placeholder: the (white) panel is only
    // built after the fetch, so the area keeps the page background while loading
    // instead of flashing a blank white box.
    const host = el("div", {}, el("p", { class: "muted" }, "Loading runs…"));
    root.appendChild(host);
    loadRuns(host);
  }

  async function loadRuns(host) {
    let runs;
    try { runs = await api.get("/api/runs"); }
    catch (e) {
      const p = el("div", { class: "panel" }); p.appendChild(errBox(e));
      clear(host); host.appendChild(p); return;
    }
    // Sort newest-first by the run_id timestamp client-side, so the order is
    // correct regardless of backend ordering (older server builds name-sort).
    runs = (runs || []).slice().sort((a, b) => {
      const ka = runSortKey(a), kb = runSortKey(b);
      if (ka === kb) return 0;
      if (!ka) return 1;
      if (!kb) return -1;
      return kb < ka ? -1 : 1;
    });
    const panel = el("div", { class: "panel" });
    const tbl = el("table", {}, el("thead", {}, el("tr", {},
      el("th", {}, "Run"), el("th", {}, "Status"), el("th", {}, "Stages"),
      el("th", {}, "Agent"), el("th", {}, "Score"))));
    const body = el("tbody", {});
    for (const r of runs) {
      const total = r.stage_total || (r.stage_count != null ? r.stage_count : ((r.passed || 0) + (r.failed || 0)));
      const prog = total ? `${r.passed || 0}/${total}` : "—";
      const agent = r.agent ? KARMA.labels.agent(r.agent) : el("span", { class: "muted" }, "none");
      const p = KARMA.labels.runName(r.run_id, r);
      body.appendChild(el("tr", { class: "clickable", onClick: () => renderDetail(r.run_id) },
        el("td", {},
          el("div", { class: "run-name" }, p.app + (p.name ? " · " + p.name : "")),
          p.ts ? el("div", { class: "muted run-ts" }, KARMA.labels.formatTs(p.ts)) : null),
        el("td", {}, statusBadge(r.status)),
        el("td", {}, prog),
        el("td", {}, agent),
        el("td", {}, scoreCell(r.judge_score))));
    }
    if (!runs.length) body.appendChild(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "No runs yet.")));
    tbl.appendChild(body);
    panel.appendChild(tbl);
    clear(host);
    host.appendChild(panel);
    // Auto-refresh while any run is still active so progress updates in place.
    if (runs.some((r) => !isTerminal(r.status))) {
      refreshTimer = setTimeout(() => {
        if (sub === "runs" && document.body.contains(host)) loadRuns(host);
      }, 3000);
    }
  }

  // --- Run detail -----------------------------------------------------------
  async function renderDetail(runId) {
    stopTimers();
    clear(root);
    // Record this run detail as the current location so a jump to a Cases
    // sub-page (via a stage click) can return here with the back arrow.
    KARMA.currentLocation = () => KARMA.showRun(runId);
    const np = KARMA.labels.runName(runId);
    const title = np.app + (np.name ? " · " + np.name : "");
    KARMA.setBreadcrumb({ back: render, crumbs: [{ label: "Results", onClick: render }, { label: title }] });
    const scoreSlot = el("div", { class: "detail-score" });
    root.appendChild(el("div", { class: "detail-head" },
      el("div", {},
        el("h2", { class: "detail-title" }, title),
        np.ts ? el("div", { class: "muted run-ts" }, KARMA.labels.formatTs(np.ts)) : null),
      scoreSlot));

    const loading = el("p", { class: "muted" }, "Loading…");
    root.appendChild(loading);
    let d;
    try { d = await api.get(`/api/run/${runId}`); }
    catch (e) { loading.remove(); root.appendChild(errBox(e)); return; }
    loading.remove();
    const cfg = d.config || {};

    // Test score (mean judge score) top-right beside the heading, larger than it.
    if (d.judge_score != null) {
      const s = d.judge_score <= 1 ? d.judge_score * 100 : d.judge_score;
      const cls = s >= 80 ? "ok" : s >= 50 ? "warn" : "bad";
      scoreSlot.appendChild(el("span", { class: "score-value " + cls }, s.toFixed(1) + "/100.0"));
    } else {
      scoreSlot.appendChild(el("span", { class: "score-value none", title: "Not judged yet" }, "—/100.0"));
    }

    const badges = el("div", { class: "toolbar" });
    badges.appendChild(statusBadge(d.status));
    badges.appendChild(el("span", { class: "badge" },
      cfg.agent ? "agent: " + KARMA.labels.agent(cfg.agent) : "no agent"));
    if (cfg.sandbox) badges.appendChild(el("span", { class: "badge" }, cfg.sandbox));
    if (d.duration_sec) badges.appendChild(el("span", { class: "muted" }, Math.round(d.duration_sec) + "s"));
    root.appendChild(badges);

    // Judge (terminal) or Cancel (running), with an inline judge log.
    const judgeLog = el("pre", { class: "log", style: "display:none" });
    const actions = el("div", { class: "toolbar" });
    if (isTerminal(d.status)) {
      actions.appendChild(el("button", { class: "btn", onClick: () => startJudge("run", runId, false, judgeLog) }, "Judge"));
      actions.appendChild(el("button", { class: "btn secondary", onClick: () => startJudge("run", runId, true, judgeLog) }, "Dry run"));
    } else {
      const cancelBtn = el("button", { class: "btn secondary" }, "Cancel");
      cancelBtn.addEventListener("click", () => {
        cancelBtn.disabled = "disabled";
        cancelBtn.textContent = "Cancelling…";
        api.post(`/api/run/${runId}/cancel`)
          .then(() => KARMA.toast("Cancelling run — it stops at the current step", "info"))
          .catch((e) => {
            KARMA.toastError(e);
            cancelBtn.disabled = null;
            cancelBtn.textContent = "Cancel";
          });
      });
      actions.appendChild(cancelBtn);
    }
    root.appendChild(actions);
    root.appendChild(judgeLog);

    const stagesPanel = el("div", { class: "panel" });
    stagesPanel.appendChild(el("h3", {}, "Stage results"));
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
          if (ev.type === "progress") {
            live.textContent += `  ${ev.message}\n`;
          } else if (ev.type === "stage_complete") {
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

    // Supplementary info, below the Stages / Live block.
    if (cfg.params && Object.keys(cfg.params).length) {
      const p = el("div", { class: "panel" });
      p.appendChild(el("h3", {}, "Config"));
      for (const [k, v] of Object.entries(cfg.params)) {
        p.appendChild(el("div", { class: "kv" }, el("span", { class: "k" }, k), el("span", {}, String(v))));
      }
      root.appendChild(p);
    }
    // Stage definitions + adversary, always shown (every stage, regardless of how
    // many the agent reached). Prefer the spec stored with the run (works for
    // inline workflows with no saved file); fall back to the saved workflow file,
    // a synthesized single-case block, or a minimal list from the stage count.
    const onStage = (s) => KARMA.showCase(s.service, s.case_name);
    if (cfg.stages && cfg.stages.length) {
      const title = cfg.stages.length > 1 ? "Stages" : "Stage";
      root.appendChild(KARMA.workflowStagesPanel({ stages: cfg.stages, adversary: cfg.adversary || [] }, title, onStage));
    } else if (cfg.service && cfg.case_name) {
      const oneStage = { stages: [{ id: "stage_1", service: cfg.service, case_name: cfg.case_name, param_overrides: cfg.params || {} }] };
      root.appendChild(KARMA.workflowStagesPanel(oneStage, "Stage", onStage));
    } else {
      // Older run without a stored spec: try its saved file, else synthesize a
      // stage list (from the known stage count) through the SAME panel component
      // so it renders identically to every other run's stage block.
      const wfRef = cfg.workflow_path
        ? String(cfg.workflow_path).split("/").pop()
        : (cfg.workflow_id && !cfg.workflow_id.includes("/") ? cfg.workflow_id + ".yaml" : null);
      const slot = el("div", {});
      root.appendChild(slot);
      const total = cfg.stage_total || (d.stages || []).length || 0;
      const synth = () => {
        if (!total) return;
        const stages = Array.from({ length: total }, (_, i) => ({ id: "stage_" + (i + 1) }));
        slot.appendChild(KARMA.workflowStagesPanel({ stages, adversary: [] }, total > 1 ? "Stages" : "Stage", onStage));
      };
      if (wfRef) {
        let handled = false;
        api.get(`/api/workflows/${wfRef}`)
          .then((wf) => { handled = true; slot.appendChild(KARMA.workflowStagesPanel(wf, "Stages", onStage)); })
          .catch(() => { if (!handled) synth(); });
      } else {
        synth();
      }
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
            // Reload the detail so the new test score appears beside the heading.
            if (targetType === "run" && !dryRun && ev.status !== "error") {
              setTimeout(() => { if (sub === "runs") renderDetail(targetPath); }, 600);
            }
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

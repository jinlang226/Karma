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
  const lastJudgeLog = {}; // runId -> last judge log text, kept across reloads
  let runsFolder = "";     // current Runs folder being browsed ("" = top level)
  let runsFilter = "";     // loose search query across all runs
  let allRuns = [];        // last-fetched runs, used by the folder/search render

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

  // Reconstruct the run's launch into a /api/run body and a /api/cli/preview
  // payload, from the run's stored config.
  function runSpec(cfg) {
    if (cfg.workflow_path) {
      return {
        body: { workflow_path: cfg.workflow_path, agent: cfg.agent || null,
                sandbox: cfg.sandbox || "local", max_attempts: cfg.max_attempts || 1 },
        preview: { command: "workflow", target: { path: cfg.workflow_path },
                   flags: { agent: cfg.agent, sandbox: cfg.sandbox } },
      };
    }
    return {
      body: { service: cfg.service, case_name: cfg.case_name, params: cfg.params || {},
              agent: cfg.agent || null, sandbox: cfg.sandbox || "local",
              agent_timeout_sec: cfg.agent_timeout_sec || 900 },
      preview: { command: "case", target: { service: cfg.service, case: cfg.case_name },
                 flags: { agent: cfg.agent, sandbox: cfg.sandbox,
                          timeout: cfg.agent_timeout_sec, params: cfg.params || {} } },
    };
  }

  // Panel showing the CLI command equivalent of a run + a "run again" button.
  // Rendered for every run so the detail page is consistent regardless of how
  // the run was started (replaces the old params-only Config block).
  function runCommandPanel(cfg) {
    const spec = runSpec(cfg);
    const code = el("pre", { class: "log" }, "Building command…");
    const copy = el("button", { class: "code-copy", title: "Copy command", onClick: () => {
      if (navigator.clipboard) navigator.clipboard.writeText(code.textContent);
      copy.textContent = "Copied"; setTimeout(() => { copy.textContent = "Copy"; }, 1200);
    } }, "Copy");
    api.post("/api/cli/preview", spec.preview)
      .then((res) => { code.textContent = res.command_multi_line || res.command_one_line || "(unavailable)"; })
      .catch(() => { code.textContent = "(could not build command)"; });

    const runBtn = el("button", { class: "btn", onClick: async () => {
      runBtn.disabled = "disabled"; runBtn.textContent = "Starting…";
      try {
        const { run_id } = await api.post("/api/run", spec.body);
        KARMA.toast("Started " + run_id, "info");
        renderDetail(run_id);
      } catch (e) {
        KARMA.toastError(e);
        runBtn.disabled = null; runBtn.textContent = "Run this test again";
      }
    } }, "Run this test again");

    return el("div", { class: "panel" },
      el("h3", {}, "Run command"),
      el("p", { class: "field-help" },
        "The command this run is equivalent to — copy it to re-run from the terminal, "
        + "or launch the same scheme again here."),
      el("div", { class: "code-block" }, copy, code),
      el("div", { class: "toolbar", style: "margin-top:12px" }, runBtn));
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
    const tabs = el("div", { class: "subtabs" },
      el("button", { class: "tab" + (sub === "runs" ? " active" : ""), onClick: () => { sub = "runs"; render(); } }, "Runs"),
      el("button", { class: "tab" + (sub === "batches" ? " active" : ""), onClick: () => { sub = "batches"; render(); } }, "Batches"));
    // "Judge all" sits at the far right of the same row -- scores every finished
    // run (objective stage-pass + LLM adjudication of regression-sweep failures).
    const judgeAll = el("button", { class: "btn secondary", onClick: () => startJudgeAll(judgeAll) }, "Judge all");
    return el("div", { class: "subtabs-row" }, tabs, judgeAll);
  }

  async function startJudgeAll(btn) {
    btn.disabled = "disabled";
    btn.textContent = "Judging…";
    try {
      const { job_id } = await api.post("/api/judge/start", { target_type: "all" });
      KARMA.toast("Judging all finished runs…", "info");
      api.stream(`/api/judge/jobs/${job_id}/stream`, {
        statusPath: `/api/judge/jobs/${job_id}`,
        onEvent: (ev) => {
          if (ev.type === "judge_progress" && ev.index && ev.total) {
            btn.textContent = `Judging ${ev.index}/${ev.total}…`;
          } else if (ev.type === "judge_complete") {
            KARMA.toast("Judge all " + (ev.status || "complete"), ev.status === "error" ? "error" : "success");
            if (sub === "runs") render();   // refresh the list with new scores
          }
        },
        onDone: () => { btn.disabled = null; btn.textContent = "Judge all"; },
      });
    } catch (e) {
      KARMA.toastError(e);
      btn.disabled = null;
      btn.textContent = "Judge all";
    }
  }

  function render() {
    stopTimers();
    clear(root);
    KARMA.replayEnter(root);
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

  // Loose, symbol-insensitive match: every whitespace token of the query must
  // appear somewhere in the run's display name / id / folder, so "mongodb tls"
  // matches "examples/mongodb · MongoDB · TLS Setup".
  function runMatches(r, tokens) {
    if (!tokens.length) return true;
    const p = KARMA.labels.runName(r.run_id, r);
    const hay = `${p.app} ${p.name || ""} ${r.run_id} ${r.dir || ""}`.toLowerCase();
    return tokens.every((t) => hay.includes(t));
  }
  // Immediate subfolders one path segment below *folder*.
  function runSubfolders(folder) {
    const prefix = folder ? folder + "/" : "";
    const subs = new Set();
    for (const r of allRuns) {
      const d = r.dir || "";
      if (!d) continue;
      if (folder ? d.startsWith(prefix) : true) {
        const rest = folder ? d.slice(prefix.length) : d;
        if (rest) subs.add(prefix + rest.split("/")[0]);
      }
    }
    return [...subs].sort();
  }
  const runsIn = (folder) => allRuns.filter((r) => (r.dir || "") === folder);
  const runsUnder = (folder) => allRuns.filter((r) => {
    const d = r.dir || "";
    return d === folder || d.startsWith(folder ? folder + "/" : "");
  });
  function openRunsFolder(folder) {
    runsFolder = folder;
    const body = document.getElementById("runs-body");
    if (body) renderRunRows(body);
  }

  // One clickable run row.
  function runRow(r, showFolder) {
    const total = r.stage_total || (r.stage_count != null ? r.stage_count : ((r.passed || 0) + (r.failed || 0)));
    const prog = total ? `${r.passed || 0}/${total}` : "—";
    const agent = r.agent ? KARMA.labels.agent(r.agent) : el("span", { class: "muted" }, "none");
    const p = KARMA.labels.runName(r.run_id, r);
    const name = el("div", {},
      el("div", { class: "run-name" }, p.app + (p.name ? " · " + p.name : "")),
      p.ts ? el("div", { class: "muted run-ts" }, KARMA.labels.formatTs(p.ts)
        + (showFolder && r.dir ? "  ·  " + r.dir : "")) : null);
    return el("tr", { class: "clickable", onClick: () => renderDetail(r.run_id) },
      el("td", {}, name),
      el("td", {}, statusBadge(r.status)),
      el("td", {}, prog),
      el("td", {}, agent),
      el("td", {}, scoreCell(r.judge_score)));
  }

  // One folder row: a clickable folder name that drills in + a run count.
  function runFolderRow(folder) {
    const open = () => openRunsFolder(folder);
    const name = folder.split("/").pop();
    return el("tr", { class: "wf-folder-row" },
      el("td", { colspan: "4" },
        el("span", { class: "crumb-link wf-folder-link", onClick: open },
          el("span", { class: "wf-folder-icon" }, "📁"), name + "/"),
        el("span", { class: "muted wf-folder-count" }, `${runsUnder(folder).length} runs`)),
      el("td", {}, el("button", { class: "btn secondary", onClick: open }, "Open")));
  }

  // Render the tbody. With a search term, show a flat loose-matched result across
  // every folder. Otherwise browse the current folder: subfolders (drill in) +
  // the runs directly in it, with a breadcrumb to step back.
  function renderRunRows(body) {
    clear(body);
    if (!allRuns.length) {
      body.appendChild(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "No runs yet.")));
      return;
    }
    const tokens = runsFilter.split(/\s+/).filter(Boolean);
    if (tokens.length) {
      const hits = allRuns.filter((r) => runMatches(r, tokens));
      if (!hits.length) {
        body.appendChild(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "No runs match your search.")));
        return;
      }
      for (const r of hits) body.appendChild(runRow(r, true));
      return;
    }
    // Breadcrumb when inside a subfolder: "← runs/examples" where "runs" and any
    // intermediate segment are clickable; the current folder segment is plain.
    if (runsFolder) {
      const parent = runsFolder.includes("/") ? runsFolder.slice(0, runsFolder.lastIndexOf("/")) : "";
      const go = (folder) => () => openRunsFolder(folder);
      const cell = el("td", { colspan: "5" },
        el("span", { class: "crumb-link", title: "Up one folder", onClick: go(parent) }, "← "),
        el("span", { class: "crumb-link", onClick: go("") }, "runs"));
      let acc = "";
      const segs = runsFolder.split("/");
      segs.forEach((seg, i) => {
        acc = acc ? acc + "/" + seg : seg;
        cell.appendChild(document.createTextNode("/"));
        cell.appendChild(i === segs.length - 1
          ? el("span", { class: "wf-crumb-current" }, seg)
          : el("span", { class: "crumb-link", onClick: go(acc) }, seg));
      });
      const row = el("tr", { class: "wf-crumb-row" }, cell);
      body.appendChild(row);
      const thead = body.parentElement && body.parentElement.querySelector("thead");
      if (thead) cell.style.top = Math.max(0, Math.round(thead.getBoundingClientRect().height) - 2) + "px";
    }
    for (const sub of runSubfolders(runsFolder)) body.appendChild(runFolderRow(sub));
    for (const r of runsIn(runsFolder)) body.appendChild(runRow(r));
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
    allRuns = (runs || []).slice().sort((a, b) => {
      const ka = runSortKey(a), kb = runSortKey(b);
      if (ka === kb) return 0;
      if (!ka) return 1;
      if (!kb) return -1;
      return kb < ka ? -1 : 1;
    });
    // Re-render rows in place if the table already exists (auto-refresh), so the
    // search box keeps focus; otherwise build the panel + search + table.
    let body = document.getElementById("runs-body");
    if (!body) {
      clear(host);
      const panel = el("div", { class: "panel" });
      const search = el("input", {
        type: "search", id: "runs-search", placeholder: "Search runs…",
        value: runsFilter, autocomplete: "off",
        onInput: (e) => {
          runsFilter = e.target.value.trim().toLowerCase();
          renderRunRows(document.getElementById("runs-body"));
        },
      });
      panel.appendChild(el("div", { class: "toolbar" }, search));
      const tbl = el("table", {}, el("thead", {}, el("tr", {},
        el("th", {}, "Run"), el("th", {}, "Status"), el("th", {}, "Stages"),
        el("th", {}, "Agent"), el("th", {}, "Score"))));
      body = el("tbody", { id: "runs-body" });
      tbl.appendChild(body);
      panel.appendChild(tbl);
      host.appendChild(panel);
    }
    renderRunRows(body);
    // Auto-refresh while any run is still active so progress updates in place.
    if (allRuns.some((r) => !isTerminal(r.status))) {
      refreshTimer = setTimeout(() => {
        if (sub === "runs" && document.body.contains(host)) loadRuns(host);
      }, 3000);
    }
  }

  // --- Run detail -----------------------------------------------------------
  async function renderDetail(runId) {
    stopTimers();
    clear(root);
    KARMA.replayEnter(root);
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
    try {
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

    // Judge (terminal) or Cancel (running), with an inline judge log. Shows the
    // live in-session log if present, else the persisted runs/<id>/judge.log.
    const judgeLog = el("pre", { class: "log", style: "display:none" });
    const persisted = lastJudgeLog[runId] || d.judge_log;
    if (persisted) { judgeLog.textContent = persisted; judgeLog.style.display = ""; }
    const actions = el("div", { class: "toolbar" });
    // A run that never finished cleanly (unknown/absent status, or interrupted)
    // cannot be judged -- surface that instead of offering a Judge button or a
    // (false) live stream.
    const st = String(d.status || "").toLowerCase();
    const unknownStatus = !d.status || st === "unknown";
    const interrupted = st === "interrupted";
    if (unknownStatus || interrupted) {
      actions.appendChild(el("div", { class: "error-box" }, interrupted
        ? "This run was interrupted — there is no complete outcome to judge."
        : "This run has an unknown status — there is no recorded outcome to judge."));
    } else if (isTerminal(d.status)) {
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
    // history, so this resumes even after navigating away and back. An
    // unknown-status run is not actually running, so skip the stream for it.
    if (!isTerminal(d.status) && !unknownStatus) {
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

    // Regression sweep: each previously-passing stage's oracle, re-run at the end
    // of a multi-stage workflow to catch stages that later stages broke. The
    // judge adjudicates each failure as a real regression or a false positive
    // (a later stage legitimately changed the same state).
    const sweep = d.regression_sweep;
    if (sweep && Object.keys(sweep).length) {
      const adj = {};
      (d.judge_breakdown && d.judge_breakdown.regressions || []).forEach((r) => { adj[r.stage_id] = r; });
      const rp = el("div", { class: "panel" });
      rp.appendChild(el("h3", {}, "Regression sweep"));
      const regressed = Object.values(sweep).filter((v) => v && v.verdict !== "pass").length;
      rp.appendChild(el("p", { class: "field-help" },
        "Every previously-passing stage's oracle, re-evaluated after the whole workflow ran. " +
        (regressed
          ? regressed + " re-check(s) failed — the judge reviews each to separate real regressions from false positives (a later stage legitimately changing the same state)."
          : "No regressions: every stage still passes.")));
      const list = el("div", { class: "stage-scroll" });
      for (const [sid, v] of Object.entries(sweep)) {
        const failed = v && v.verdict !== "pass";
        const st = KARMA.labels.status((v && v.verdict) || "unknown");
        const a = adj[sid];
        // Verdicts grouped at the right: the judge's adjudication (left) next to
        // the oracle re-check (right), so the pass/fail badge is always in the
        // same place whether or not there is a judge badge.
        const verdicts = el("div", { class: "sweep-verdicts" });
        if (failed && a) {
          verdicts.appendChild(el("span", { class: "badge " + (a.legitimate ? "bad" : "ok") },
            a.legitimate ? "judge: real regression" : "judge: false positive"));
        }
        verdicts.appendChild(el("span", { class: "badge " + (st.cls || "") }, st.text));
        const row = el("div", { class: "builder-row" },
          el("div", { class: "builder-row-head" }, el("span", {}, KARMA.humanize(sid)), verdicts));
        if (a && a.reasoning) row.appendChild(el("div", { class: "sweep-reason muted" }, a.reasoning));
        if (v && v.output) row.appendChild(el("pre", { class: "log" }, String(v.output)));
        list.appendChild(row);
      }
      rp.appendChild(list);
      if (d.judge_breakdown && d.judge_breakdown.summary) {
        rp.appendChild(el("p", { class: "field-help sweep-summary" }, "Score: " + d.judge_breakdown.summary));
      }
      root.appendChild(rp);
    }

    // Stage definitions + adversary, always shown (every stage, regardless of how
    // many the agent reached). Prefer the spec stored with the run (works for
    // inline workflows with no saved file); fall back to the saved workflow file,
    // a synthesized single-case block, or a minimal list from the stage count.
    const onStage = (s) => KARMA.showCase(s.service, s.case_name);
    const TITLE = "Test details";
    if (cfg.stages && cfg.stages.length) {
      root.appendChild(KARMA.workflowStagesPanel({ stages: cfg.stages, adversary: cfg.adversary || [] }, TITLE, onStage, true));
    } else if (cfg.service && cfg.case_name) {
      const oneStage = { stages: [{ id: "stage_1", service: cfg.service, case_name: cfg.case_name, param_overrides: cfg.params || {} }] };
      root.appendChild(KARMA.workflowStagesPanel(oneStage, TITLE, onStage, true));
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
        slot.appendChild(KARMA.workflowStagesPanel({ stages, adversary: [] }, TITLE, onStage, true));
      };
      if (wfRef) {
        let handled = false;
        api.get(`/api/workflows/${wfRef}`)
          .then((wf) => { handled = true; slot.appendChild(KARMA.workflowStagesPanel(wf, TITLE, onStage, true)); })
          .catch(() => { if (!handled) synth(); });
      } else {
        synth();
      }
    }

    // Run command: the CLI equivalent of this run + a button to launch the same
    // scheme again. Shown for every run regardless of how it was started, so the
    // page is consistent (replaces the old params-only "Config" block).
    root.appendChild(runCommandPanel(cfg));
    } catch (err) {
      // Never leave a blank page: surface the render error instead.
      root.appendChild(errBox(err));
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
            const extra = ev.message ? "  " + ev.message : "";
            log.textContent += `  ${where}: verdict=${ev.verdict ?? "-"} score=${ev.score ?? "-"}${extra}\n`;
          } else if (ev.type === "judge_complete") {
            log.textContent += `judge ${ev.status}\n`;
            KARMA.toast("Judge " + (ev.status || "complete"), ev.status === "error" ? "error" : "success");
            // Reload the detail so the new test score appears beside the heading.
            if (targetType === "run" && !dryRun && ev.status !== "error") {
              setTimeout(() => { if (sub === "runs") renderDetail(targetPath); }, 600);
            }
          }
          if (targetType === "run") lastJudgeLog[targetPath] = log.textContent;
          log.scrollTop = log.scrollHeight;
        },
        onDone: () => {
          log.textContent += "— judge stream ended —\n";
          if (targetType === "run") lastJudgeLog[targetPath] = log.textContent;
        },
      });
    } catch (e) {
      log.textContent += "Error: " + e.message + "\n";
      if (targetType === "run") lastJudgeLog[targetPath] = log.textContent;
      KARMA.toastError(e);
    }
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

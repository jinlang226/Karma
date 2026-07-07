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
  let activeJudgeJob = null; // job_id while a "Judge all" is running (else null)
  let activeJudgeMode = null; // "wo" | "w" -- which mode's judge-all is running
  let judgeCancelling = false; // true between a cancel click and the job ending
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
  // Rebuild a minimal workflow YAML from a run's recorded stages -- for runs
  // launched as an inline workflow, where there is no saved file for the CLI to
  // point at. config.json records each stage's case as `case_name`; the input
  // YAML key is `case`.
  function workflowYamlFromConfig(cfg) {
    const stages = cfg.stages || [];
    const out = ["metadata:", "  id: " + (cfg.workflow_id || "workflow"), "spec:"];
    if (cfg.agent_session) out.push("  agent_session: " + cfg.agent_session);
    if (cfg.prompt_mode) out.push("  prompt_mode: " + cfg.prompt_mode);
    out.push("  stages:");
    stages.forEach((s, i) => {
      out.push("    - id: " + (s.id || "stage_" + (i + 1)));
      out.push("      service: " + s.service);
      out.push("      case: " + s.case_name);
      const po = s.param_overrides || {};
      const keys = Object.keys(po);
      if (keys.length) {
        out.push("      param_overrides:");
        keys.forEach((k) => out.push("        " + k + ": " + JSON.stringify(po[k])));
      }
    });
    return out.join("\n") + "\n";
  }

  // Map a run's config.json to a re-run request + CLI preview. Three shapes:
  // a saved workflow file, a single case (recorded top-level OR a one-stage
  // workflow), or an inline multi-stage workflow (no CLI file -> show its YAML).
  function runSpec(cfg) {
    const stages = cfg.stages || [];
    if (cfg.workflow_path) {
      return {
        body: { workflow_path: cfg.workflow_path, agent: cfg.agent || null,
                sandbox: cfg.sandbox || "local", max_attempts: cfg.max_attempts || 1 },
        preview: { command: "workflow", target: { path: cfg.workflow_path },
                   flags: { agent: cfg.agent, sandbox: cfg.sandbox } },
      };
    }
    const svc = cfg.service || (stages.length === 1 ? stages[0].service : "");
    const cas = cfg.case_name || (stages.length === 1 ? stages[0].case_name : "");
    if (svc && cas) {
      const params = cfg.params
        || (stages.length === 1 ? (stages[0].param_overrides || {}) : {});
      return {
        body: { service: svc, case_name: cas, params, agent: cfg.agent || null,
                sandbox: cfg.sandbox || "local", agent_timeout_sec: cfg.agent_timeout_sec || 900 },
        preview: { command: "case", target: { service: svc, case: cas },
                   flags: { agent: cfg.agent, sandbox: cfg.sandbox,
                            timeout: cfg.agent_timeout_sec, params } },
      };
    }
    return {
      body: { workflow_yaml: workflowYamlFromConfig(cfg), agent: cfg.agent || null,
              sandbox: cfg.sandbox || "local", max_attempts: cfg.max_attempts || 1 },
      inline: { id: cfg.workflow_id || "workflow", count: stages.length,
                yaml: workflowYamlFromConfig(cfg) },
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
    if (spec.inline) {
      // Inline multi-stage workflow: no CLI file exists, so show the rebuilt YAML
      // plus the run-workflow line for it. "Run this test again" re-runs it inline.
      code.textContent =
        "# Inline " + spec.inline.count + "-stage workflow '" + spec.inline.id
        + "' (no saved file). Save this YAML to e.g. workflows/" + spec.inline.id
        + ".yaml, then:\n#   python orchestrator.py run-workflow workflows/"
        + spec.inline.id + ".yaml --agent " + (cfg.agent || "<agent>")
        + " --sandbox " + (cfg.sandbox || "local") + "\n\n" + spec.inline.yaml;
    } else {
      api.post("/api/cli/preview", spec.preview)
        .then((res) => { code.textContent = res.command_multi_line || res.command_one_line || "(unavailable)"; })
        .catch(() => { code.textContent = "(could not build command)"; });
    }

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
    else {
      // Re-entering Results from another tab lands on the homepage (top-level
      // folder list), not the sub-folder/detail last viewed. Reset the browse
      // state; a deep-link (pendingRun) above is the only thing that skips this.
      runsFolder = "";
      runsFilter = "";
      render();
    }
    // Re-adopt an in-flight "Judge all" that outlived a page refresh.
    reattachActiveJudge();
  }

  function stopTimers() { if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; } }

  // Search is scoped to the folder currently being browsed (like "Judge all"):
  // the placeholder states the scope so it's clear the query is folder-limited.
  function searchPlaceholder() {
    return runsFolder ? `Search runs in ${runsFolder}…` : "Search runs…";
  }

  function subtabs() {
    const tabs = el("div", { class: "subtabs" },
      el("button", { class: "tab" + (sub === "runs" ? " active" : ""), onClick: () => { sub = "runs"; render(); } }, "Runs"),
      el("button", { class: "tab" + (sub === "batches" ? " active" : ""), onClick: () => { sub = "batches"; render(); } }, "Batches"));
    // Two folder-scoped "Judge all" buttons grouped at the right (where the single
    // Judge-all button used to sit): objective (w/o rubric) and rubric (w/ rubric,
    // scored against the bundled example rubric). Only one runs at a time; the
    // running one becomes Cancel and the other is disabled.
    const scopeTip = runsFolder ? ` for every finished run in "${runsFolder}"` : " for every finished run";
    const btnWo = el("button", { id: "judge-all-wo", class: "btn secondary",
      title: "Objective stage-pass score + regression adjudication" + scopeTip,
      onClick: () => onJudgeAllClick("wo") }, "Judge all w/o Rubric");
    const btnW = el("button", { id: "judge-all-w", class: "btn secondary",
      title: "LLM-score each oracle-passing stage against the rubric" + scopeTip,
      onClick: () => onJudgeAllClick("w") }, "Judge all w/ Rubric");
    applyJudgeAllButtons(btnWo, btnW);
    return el("div", { class: "subtabs-row" }, tabs,
      el("div", { class: "judge-all-group" }, btnWo, btnW));
  }

  // Click a Judge-all button: start that mode, or (if it's the running one) cancel.
  function onJudgeAllClick(mode) {
    if (activeJudgeJob) { if (activeJudgeMode === mode) cancelJudgeAll(); }
    else startJudgeAll(mode);
  }

  function judgeProgressLabel(i, n) {
    return `Cancel judging${i != null && n != null ? ` (${i}/${n})` : "…"}`;
  }
  const judgeAllModeBtn = (mode) => document.getElementById(mode === "wo" ? "judge-all-wo" : "judge-all-w");
  const judgeBtn = () => (activeJudgeMode ? judgeAllModeBtn(activeJudgeMode) : null);

  // Reflect running/idle state on the two buttons. Pass refs at build time
  // (before they're in the DOM); later calls look them up by id.
  // Idle label with the browsed-folder scope as a highlighted chip
  // ("Judge all w/o Rubric in [short/r6]"), matching the old single-button pattern.
  function setScopedJudgeLabel(btn, base) {
    clear(btn);
    btn.appendChild(document.createTextNode(base));
    if (runsFolder) {
      btn.appendChild(document.createTextNode(" in "));
      btn.appendChild(el("span", { class: "judge-scope" }, runsFolder));
    }
  }

  function applyJudgeAllButtons(bw, bwr) {
    const map = { wo: bw || judgeAllModeBtn("wo"), w: bwr || judgeAllModeBtn("w") };
    for (const mode of ["wo", "w"]) {
      const b = map[mode];
      if (!b) continue;
      const idle = mode === "wo" ? "Judge all w/o Rubric" : "Judge all w/ Rubric";
      if (activeJudgeMode === mode) {          // the running one -> Cancel
        b.disabled = null;
        b.textContent = judgeCancelling ? "Cancelling…" : judgeProgressLabel();
      } else {                                 // idle, or disabled while the other runs
        b.disabled = activeJudgeJob ? "disabled" : null;
        setScopedJudgeLabel(b, idle);
      }
    }
  }

  // Subscribe to a running job's SSE stream. Handlers look the active button up
  // by id (not a captured reference) so they survive re-renders and a refresh.
  function attachJudgeStream(jobId) {
    api.stream(`/api/judge/jobs/${jobId}/stream`, {
      statusPath: `/api/judge/jobs/${jobId}`,
      onEvent: (ev) => {
        const b = judgeBtn();
        if (ev.type === "judge_scan") {
          if (!ev.to_judge) {
            KARMA.toast(`All ${ev.already_scored || 0} finished runs already judged — nothing to do.`, "info");
          } else {
            if (b && !judgeCancelling) b.textContent = judgeProgressLabel(0, ev.to_judge);
            KARMA.toast(
              `Judging ${ev.to_judge} unjudged run${ev.to_judge === 1 ? "" : "s"}` +
              (ev.llm_count ? ` — ${ev.static_count} scored instantly, ${ev.llm_count} need LLM adjudication` : "") +
              ` (${ev.already_scored || 0} already scored).`, "info");
          }
        } else if (ev.type === "judge_progress" && ev.index && ev.total) {
          if (b && !judgeCancelling) b.textContent = judgeProgressLabel(ev.index, ev.total);
        } else if (ev.type === "judge_complete") {
          const st = ev.status || "complete";
          KARMA.toast("Judge all " + st, st === "error" ? "error" : (st === "cancelled" ? "info" : "success"));
        }
      },
      onDone: () => {
        activeJudgeJob = null; activeJudgeMode = null; judgeCancelling = false;
        if (sub === "runs") render();   // refresh scores (incl. partial) + reset buttons
        else applyJudgeAllButtons();
      },
    });
  }

  async function startJudgeAll(mode) {
    activeJudgeMode = mode;
    const b = judgeAllModeBtn(mode);
    if (b) { b.disabled = "disabled"; b.textContent = "Starting…"; }
    applyJudgeAllButtons();                     // disable the other mode
    try {
      const body = { target_type: "all", target_path: runsFolder };
      if (mode === "w") body.use_default_rubric = true;  // score against the bundled example
      const resp = await api.post("/api/judge/start", body);
      activeJudgeJob = resp.job_id;
      judgeCancelling = false;
      const bb = judgeBtn(); if (bb) { bb.disabled = null; bb.textContent = judgeProgressLabel(); }
      attachJudgeStream(resp.job_id);
    } catch (e) {
      KARMA.toastError(e);
      activeJudgeJob = null; activeJudgeMode = null; judgeCancelling = false;
      applyJudgeAllButtons();
    }
  }

  // After a page refresh the backend job keeps running in its thread but the
  // in-page state is gone. Re-adopt any running "Judge all" (and its mode) so the
  // right button reflects reality; the SSE replay repopulates progress.
  async function reattachActiveJudge() {
    if (activeJudgeJob) return;
    let jobs;
    try { jobs = await api.get("/api/judge/jobs"); } catch (_) { return; }
    const running = (jobs || []).filter((j) => j.target_type === "all" && j.status === "running");
    if (!running.length) return;
    const job = running[running.length - 1];   // newest
    activeJudgeJob = job.job_id;
    activeJudgeMode = job.has_rubric ? "w" : "wo";
    judgeCancelling = false;
    applyJudgeAllButtons();
    attachJudgeStream(activeJudgeJob);
  }

  async function cancelJudgeAll() {
    if (!activeJudgeJob) return;
    judgeCancelling = true;
    const b = judgeBtn();
    if (b) { b.disabled = "disabled"; b.textContent = "Cancelling…"; }
    try {
      await api.post(`/api/judge/jobs/${activeJudgeJob}/cancel`, {});
      // The job ends after its current run; the stream's judge_complete + onDone reset.
    } catch (e) {
      KARMA.toastError(e);
      judgeCancelling = false;           // cancel POST failed -> let it keep running
      if (b) { b.disabled = null; b.textContent = judgeProgressLabel(); }
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
    setFolderCrumb();
    // Title bar with a live "N running" badge at the top-left: a global tally of
    // every run currently executing (status "running"), across all folders. The
    // span is refreshed in place by updateRunningTotal() on each runs fetch.
    root.appendChild(el("h2", { class: "results-title" }, "Results",
      el("span", { id: "running-total", class: "running-total" })));
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
  // Compact run-status summary for a folder: one badge per distinct status
  // (e.g. "complete 4", "failed 1", "running 7") tallied over every run under the
  // folder -- the status each run/case returns. Null when the folder has no runs.
  function folderStatusSummary(folder) {
    const runs = runsUnder(folder);
    if (!runs.length) return null;
    const counts = {};
    for (const r of runs) {
      const id = (r.status || "unknown");
      counts[id] = (counts[id] || 0) + 1;
    }
    const wrap = el("span", { class: "folder-status" });
    Object.keys(counts).sort().forEach((id) => {
      const st = KARMA.labels.status(id);
      wrap.appendChild(el("span", { class: "badge " + st.cls, title: st.text }, `${st.text} ${counts[id]}`));
    });
    return wrap;
  }

  // Refresh the top-left "N running" badge from allRuns. A run counts as running
  // while it is NOT in a terminal status (the same active set that drives the 3s
  // auto-refresh) — so "Running", "Setting up" and just-started runs all count.
  // Always shown (including "0 running") so the homepage states the live total.
  function updateRunningTotal() {
    const node = document.getElementById("running-total");
    if (!node) return;
    const n = allRuns.filter((r) => !isTerminal(r.status)).length;
    clear(node);
    const st = KARMA.labels.status("running");
    node.appendChild(el("span", {
      class: "badge " + st.cls,
      title: n + " run" + (n === 1 ? "" : "s") + " currently running",
    }, `${n} running`));
  }

  // Set the top-left breadcrumb for the current runsFolder: "Results / <folder…>"
  // plus the folder status summary. Shared by render() (folder-crumb-click path)
  // and openRunsFolder() (folder-row-click path) so both stay in sync; cleared at
  // the top level where the "Results" heading is the title.
  function setFolderCrumb() {
    if (!(runsFolder && sub === "runs")) { KARMA.setBreadcrumb(null); return; }
    const goFolder = (folder) => () => { runsFolder = folder; sub = "runs"; render(); };
    const crumbs = [{ label: "Results", onClick: goFolder("") }];
    let acc = "";
    const segs = runsFolder.split("/");
    segs.forEach((seg, i) => {
      acc = acc ? acc + "/" + seg : seg;
      crumbs.push(i === segs.length - 1 ? { label: seg } : { label: seg, onClick: goFolder(acc) });
    });
    const parent = runsFolder.includes("/") ? runsFolder.slice(0, runsFolder.lastIndexOf("/")) : "";
    KARMA.setBreadcrumb({ back: goFolder(parent), crumbs, suffix: folderStatusSummary(runsFolder) });
  }

  function openRunsFolder(folder) {
    runsFolder = folder;
    setFolderCrumb();
    // This partial-render path (folder-row click) does not rebuild subtabs(), so
    // refresh the folder-scope chip on both Judge-all buttons and the search
    // placeholder here.
    applyJudgeAllButtons();
    const sb = document.getElementById("runs-search");
    if (sb) sb.placeholder = searchPlaceholder();
    const body = document.getElementById("runs-body");
    if (body) {
      renderRunRows(body);
      KARMA.replayEnter(document.getElementById("runs-crumb-bar"), "fadeIn 0.25s ease both");
      KARMA.replayEnter(body, "fadeIn 0.25s ease both");
    }
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
      el("td", { class: "score-cell" }, scoreCell(r.judge_score)),
      el("td", { class: "score-cell" }, scoreCell(r.judge_score_rubric)));
  }

  // One folder row: a clickable folder name that drills in + a run count.
  function runFolderRow(folder) {
    const open = () => openRunsFolder(folder);
    const name = folder.split("/").pop();
    return el("tr", { class: "wf-folder-row" },
      el("td", { colspan: "5" },
        el("span", { class: "crumb-link wf-folder-link", onClick: open },
          el("span", { class: "wf-folder-icon" }, "📁"), name),
        el("span", { class: "muted wf-folder-count" }, `${runsUnder(folder).length} runs`)),
      el("td", {}, el("button", { class: "btn secondary", onClick: open }, "Open")));
  }

  // Fill (or hide) the current-folder bar above the runs list. This in-page bar
  // is kept ALONGSIDE the top-left breadcrumb (set in render()) -- both ways of
  // showing the current folder are available.
  function renderRunsCrumbBar() {
    const bar = document.getElementById("runs-crumb-bar");
    if (!bar) return;
    clear(bar);
    if (!runsFolder) { bar.style.display = "none"; return; }
    const parent = runsFolder.includes("/") ? runsFolder.slice(0, runsFolder.lastIndexOf("/")) : "";
    const go = (folder) => () => openRunsFolder(folder);
    bar.appendChild(el("span", { class: "crumb-link dir-up", title: "Up one folder", onClick: go(parent) }, "←"));
    bar.appendChild(el("span", { class: "crumb-link", onClick: go("") }, "runs"));
    let acc = "";
    runsFolder.split("/").forEach((seg, i, segs) => {
      acc = acc ? acc + "/" + seg : seg;
      bar.appendChild(el("span", { class: "crumb-sep" }, "/"));
      bar.appendChild(i === segs.length - 1
        ? el("span", { class: "wf-crumb-current" }, seg)
        : el("span", { class: "crumb-link", onClick: go(acc) }, seg));
    });
    bar.style.display = "";
  }

  // Render the tbody. With a search term, show a flat loose-matched result across
  // every folder. Otherwise browse the current folder: subfolders (drill in) +
  // the runs directly in it, with a breadcrumb to step back.
  function renderRunRows(body) {
    clear(body);
    if (!allRuns.length) {
      body.appendChild(el("tr", {}, el("td", { colspan: "6", class: "muted" }, "No runs yet.")));
      return;
    }
    const tokens = runsFilter.split(/\s+/).filter(Boolean);
    if (tokens.length) {
      const bar = document.getElementById("runs-crumb-bar");
      if (bar) { clear(bar); bar.style.display = "none"; }
      // Scope the search to the browsed folder (recursively); top level = all.
      const hits = runsUnder(runsFolder).filter((r) => runMatches(r, tokens));
      if (!hits.length) {
        body.appendChild(el("tr", {}, el("td", { colspan: "6", class: "muted" }, "No runs match your search.")));
        return;
      }
      for (const r of hits) body.appendChild(runRow(r, true));
      return;
    }
    renderRunsCrumbBar();
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
    updateRunningTotal();
    // Re-render rows in place if the table already exists (auto-refresh), so the
    // search box keeps focus; otherwise build the panel + search + table.
    let body = document.getElementById("runs-body");
    const firstBuild = !body;
    if (!body) {
      clear(host);
      const panel = el("div", { class: "panel" });
      const search = el("input", {
        type: "search", id: "runs-search", placeholder: searchPlaceholder(),
        value: runsFilter, autocomplete: "off",
        onInput: (e) => {
          runsFilter = e.target.value.trim().toLowerCase();
          renderRunRows(document.getElementById("runs-body"));
        },
      });
      panel.appendChild(el("div", { class: "toolbar" }, search));
      panel.appendChild(el("div", { id: "runs-crumb-bar", class: "dir-bar", style: "display:none" }));
      const tbl = el("table", {}, el("thead", {}, el("tr", {},
        el("th", {}, "Run"), el("th", {}, "Status"), el("th", {}, "Stages"),
        el("th", {}, "Agent"),
        el("th", { class: "score-col" }, "Score", el("br"), "w/o Rubric"),
        el("th", { class: "score-col" }, "Score", el("br"), "w/ Rubric"))));
      body = el("tbody", { id: "runs-body" });
      tbl.appendChild(body);
      panel.appendChild(tbl);
      host.appendChild(panel);
    }
    renderRunRows(body);
    // Refresh the folder status summary now that allRuns is freshly fetched (the
    // render()-time call may have run against a stale/empty cache), and keep it
    // current as runs complete during the 3s auto-refresh.
    setFolderCrumb();
    // Fade the list in on first build only (not on the 3s auto-refresh).
    if (firstBuild) KARMA.replayEnter(body, "fadeIn 0.3s ease both");
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
    // Breadcrumb: Results / <folder…> / <run>, with Results and each folder
    // segment clickable (drilling the runs list into that folder). The folder
    // comes from the cached runs list (and is refreshed from d.dir after fetch).
    const goFolder = (folder) => () => { runsFolder = folder; sub = "runs"; render(); };
    const buildCrumb = (dir) => {
      const crumbs = [{ label: "Results", onClick: goFolder("") }];
      let acc = "";
      (dir ? dir.split("/") : []).forEach((seg) => {
        acc = acc ? acc + "/" + seg : seg;
        const f = acc;
        crumbs.push({ label: seg, onClick: goFolder(f) });
      });
      crumbs.push({ label: title });
      KARMA.setBreadcrumb({ back: goFolder(dir || ""), crumbs });
    };
    buildCrumb(((allRuns.find((r) => r.run_id === runId) || {}).dir) || "");
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
    // Refresh the breadcrumb folder from the authoritative detail (covers a
    // deep-linked run where the cached runs list was empty at first render).
    if (d.dir) buildCrumb(d.dir);

    // Two test scores top-right beside the heading: objective (w/o rubric) and
    // rubric (w/ rubric). Each shows "—" until the run is judged that way.
    const scoreValue = (label, val) => {
      const wrap = el("span", { class: "score-pair" }, el("span", { class: "score-pair-label" }, label));
      if (val == null) {
        wrap.appendChild(el("span", { class: "score-value none", title: label + " — not judged yet" }, "—/100.0"));
      } else {
        const s = val <= 1 ? val * 100 : val;
        const cls = s >= 80 ? "ok" : s >= 50 ? "warn" : "bad";
        wrap.appendChild(el("span", { class: "score-value " + cls }, s.toFixed(1) + "/100.0"));
      }
      return wrap;
    };
    scoreSlot.appendChild(scoreValue("w/o Rubric", d.judge_score));
    scoreSlot.appendChild(scoreValue("w/ Rubric", d.judge_score_rubric));

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
      // The judge buttons share one in-flight slot: while a judge runs its own
      // button becomes "Cancel judging…" (click to stop) and the others are
      // disabled. Cancel stops a long rubric judge between stages.
      let judgeJob = null;      // active judge job_id, or null when idle
      let runningBtn = null;    // the button that started it
      const jbtns = [];
      const restoreJudge = () => {
        judgeJob = null; runningBtn = null;
        jbtns.forEach((b) => { b.disabled = null; b.textContent = b._label; });
      };
      const cancelJudge = async (b) => {
        b.disabled = "disabled"; b.textContent = "Cancelling…";
        try { await api.post(`/api/judge/jobs/${judgeJob}/cancel`, {}); }
        catch (e) { KARMA.toastError(e); b.disabled = null; b.textContent = "Cancel judging…"; }
      };
      const mkJudge = (label, cls, tip, dryRun, withRubric) => {
        const b = el("button", { class: cls, title: tip, onClick: () => {
          if (judgeJob) { if (b === runningBtn) cancelJudge(b); return; }
          jbtns.forEach((o) => { if (o !== b) o.disabled = "disabled"; });
          b.textContent = "Starting…";
          startJudge("run", runId, dryRun, judgeLog, withRubric, {
            onJob: (id) => { judgeJob = id; runningBtn = b; b.disabled = null; b.textContent = "Cancel judging…"; },
            onEnd: restoreJudge,
          });
        } }, label);
        b._label = label;
        jbtns.push(b);
        return b;
      };
      actions.appendChild(mkJudge("Judge w/o Rubric", "btn", "Objective stage-pass score + regression adjudication", false, false));
      actions.appendChild(mkJudge("Judge w/ Rubric", "btn", "LLM-score each oracle-passing stage against the bundled example rubric", false, true));
      actions.appendChild(mkJudge("Dry run", "btn secondary", "Assemble the judge without calling the LLM or writing results", true, false));
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

    // Rubric per-stage scores (present only when the run was judged w/ a rubric)
    // are surfaced inside each stage's row in Stage results (a badge + a section
    // in "view details"), via KARMA.stageDetail below.
    const rbBreakdown = d.judge_rubric_breakdown;
    const rubricByStage = {};
    ((rbBreakdown && rbBreakdown.stage_scores) || []).forEach((e) => { if (e.stage_id) rubricByStage[e.stage_id] = e; });

    const stagesPanel = el("div", { class: "panel" });
    stagesPanel.appendChild(el("h3", {}, "Stage results"));
    if (rbBreakdown && rbBreakdown.summary) {
      stagesPanel.appendChild(el("p", { class: "field-help" }, "Rubric — " + rbBreakdown.summary));
    }
    const host = el("div", {});
    stagesPanel.appendChild(host);
    root.appendChild(stagesPanel);

    const byId = {};
    (d.stages || []).forEach((s) => { if (s.stage_id) byId[s.stage_id] = s; });
    function renderStages() {
      clear(host);
      const list = Object.values(byId);
      if (!list.length) { host.appendChild(el("p", { class: "muted" }, "No stages yet.")); return; }
      for (const s of list) host.appendChild(KARMA.stageDetail(runId, s, rubricByStage[s.stage_id]));
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
  async function startJudge(targetType, targetPath, dryRun, log, withRubric, hooks) {
    hooks = hooks || {};
    log.style.display = "";
    log.textContent = `${dryRun ? "Dry-" : ""}judging ${targetPath}${withRubric ? " (w/ rubric)" : ""}\n`;
    try {
      const body = { target_type: targetType, target_path: targetPath, dry_run: dryRun };
      if (withRubric) body.use_default_rubric = true;  // score against the bundled example
      const { job_id } = await api.post("/api/judge/start", body);
      if (hooks.onJob) hooks.onJob(job_id);            // lets the caller flip its button to Cancel
      log.textContent += "job " + job_id + "\n";
      api.stream(`/api/judge/jobs/${job_id}/stream`, {
        statusPath: `/api/judge/jobs/${job_id}`,
        onEvent: (ev) => {
          if (ev.type === "judge_log") {
            log.textContent += ev.line + "\n";           // detailed per-line judge log
          } else if (ev.type === "judge_progress") {
            const where = ev.stage_id ? `${ev.run_id}/${ev.stage_id}` : ev.run_id;
            const extra = ev.message ? "  " + ev.message : "";
            log.textContent += `  ${where}: verdict=${ev.verdict ?? "-"} score=${ev.score ?? "-"}${extra}\n`;
          } else if (ev.type === "judge_complete") {
            log.textContent += `judge ${ev.status}${ev.up_to_date ? " (up to date)" : ""}\n`;
            if (ev.up_to_date) {
              KARMA.toast("Already up to date — not re-judged", "info");
            } else {
              KARMA.toast("Judge " + (ev.status || "complete"),
                ev.status === "error" ? "error" : (ev.status === "cancelled" ? "info" : "success"));
            }
            // Reload the detail so the new score appears -- skip on cancel / up to
            // date (nothing changed; restore the buttons via onEnd instead).
            if (targetType === "run" && !dryRun && ev.status === "complete" && !ev.up_to_date) {
              setTimeout(() => { if (sub === "runs") renderDetail(targetPath); }, 600);
            }
          }
          if (targetType === "run") lastJudgeLog[targetPath] = log.textContent;
          log.scrollTop = log.scrollHeight;
        },
        onDone: () => {
          log.textContent += "— judge stream ended —\n";
          if (targetType === "run") lastJudgeLog[targetPath] = log.textContent;
          if (hooks.onEnd) hooks.onEnd();               // restore the buttons
        },
      });
    } catch (e) {
      log.textContent += "Error: " + e.message + "\n";
      if (targetType === "run") lastJudgeLog[targetPath] = log.textContent;
      KARMA.toastError(e);
      if (hooks.onEnd) hooks.onEnd();
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

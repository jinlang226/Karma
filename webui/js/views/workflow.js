/*
 * KARMA web UI -- Workflow view.
 *
 * Three panels:
 *   - Files: the workflow YAML files on disk (/api/workflows), each runnable.
 *   - Builder: add stages (service + case + param overrides), optionally add
 *     adversary injections (scenario + inject/lift stage), pick a prompt
 *     mode, generate YAML, validate it via /api/workflow/import, and run it
 *     inline through /api/run with a workflow_yaml payload.
 *   - Jobs: the active job list (/api/jobs) plus a live log for a started run.
 *
 * Adversary injection is an option of building a workflow (it lives in the
 * builder under spec.adversary), not a separate feature.
 */
(function () {
  "use strict";
  const KARMA = window.KARMA;
  const { el, clear, api } = KARMA;

  let root;
  let pendingWorkflow = null;   // set by KARMA.showWorkflow to deep-link a detail
  let pendingAdvScenario = null;   // set by KARMA.useScenarioInBuilder ({scenario, overrides})
  let services = [];
  let agents = [];      // available agent names, for the run-config selectors
  let runAgent = "";    // agent applied to workflow runs ("" = no agent)
  let runSandbox = "local";
  let runMaxAttempts = 1;   // workflow-level retry cap (1 = no retry)
  let builderId = "ui-workflow";  // default name for builder saves (set when customizing)
  const selected = new Set();      // workflow paths checked for "Run selected"
  let allFiles = [];    // every workflow file from /api/workflows (cached for search)
  let wfFilter = "";    // current Saved-Workflows search term (lowercased)
  let wfFolder = "";    // folder currently being browsed ("" = workflows/ root)
  let pendingFolder = ""; // folder to open on the next list render (set by back-nav)
  let scenarios = [];   // available adversary scenarios: {service, scenario, has_lift}
  let stages = [];      // builder stage rows: {service, case, overrides}
  let advRows = [];     // builder adversary rows: {scenario, injectIndex, liftIndex}

  function errBox(e) {
    const m = e.message || String(e);
    KARMA.toast(m, "error");
    return el("div", { class: "error-box" }, m);
  }

  async function mount(container) {
    root = container;
    stages = [];   // start each mount with a fresh builder
    advRows = [];
    if (!services.length) {
      try { services = (await api.get("/api/services")).services || []; } catch (_e) { services = []; }
    }
    if (!agents.length) {
      try { agents = await api.get("/api/agents"); } catch (_e) { agents = []; }
    }
    try { scenarios = await api.get("/api/adversary/scenarios") || []; } catch (_e) { scenarios = []; }
    if (pendingWorkflow) { const pw = pendingWorkflow; pendingWorkflow = null; renderWorkflowDetail(pw.name, pw.path); return; }
    let scrollToAdversary = false;
    if (pendingAdvScenario) {
      const ps = pendingAdvScenario; pendingAdvScenario = null;
      // Seed the builder with this adversary injection (params carried over).
      advRows = [{ scenario: ps.scenario, injectIndex: 0, liftIndex: -1, overrides: ps.overrides || {} }];
      scrollToAdversary = true;
      KARMA.toast("Adversary added to the builder — add a stage, then set its inject/lift points.", "info");
    }
    render();
    // Arriving from a scenario's "Use in a workflow": jump straight to the
    // injection section of the builder rather than the top of the page.
    if (scrollToAdversary) {
      setTimeout(() => {
        const target = document.getElementById("wf-adversary");
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 0);
    }
  }

  // Cross-view deep link: open a specific saved workflow's detail (used by the
  // back stack so returning from a Cases sub-page lands on the exact workflow).
  KARMA.showWorkflow = function (name, path) { pendingWorkflow = { name, path }; KARMA.activate("workflow"); };

  // Cross-view: open the builder pre-seeded with an adversary injection for the
  // given scenario + entered param overrides (from the Cases scenario page).
  KARMA.useScenarioInBuilder = function (scenario, overrides) {
    pendingAdvScenario = { scenario, overrides: overrides || {} };
    KARMA.activate("workflow");
  };

  function render() {
    clear(root);
    KARMA.replayEnter(root);
    KARMA.setBreadcrumb(null);   // back to the list -> drop any "Workflows / ..." crumb
    root.appendChild(el("h2", {}, "Workflows"));
    root.appendChild(runConfigPanel());
    root.appendChild(filesPanel());
    root.appendChild(builderPanel());
    root.appendChild(jobsPanel());
  }

  // Agent + sandbox applied to every workflow run started from this page.
  // Without this, workflow runs went out with no agent and always failed.
  function runConfigPanel() {
    // Default to the first registered agent (a workflow with no agent just runs
    // setup and fails the oracle, so it is not offered as a choice).
    if (!runAgent && agents.length) runAgent = agents[0];
    const agentSel = el("select", { onChange: (e) => { runAgent = e.target.value; } },
      ...agents.map((a) => el("option", {
        value: a, selected: a === runAgent ? "selected" : null,
      }, KARMA.labels.agent(a))));
    const sandboxSel = el("select", { onChange: (e) => { runSandbox = e.target.value; } },
      el("option", { value: "local", selected: runSandbox === "local" ? "selected" : null }, "Local"),
      el("option", { value: "docker", selected: runSandbox === "docker" ? "selected" : null }, "Docker"));
    const attemptsInput = el("input", {
      type: "number", min: "1", step: "1", value: String(runMaxAttempts),
      onChange: (e) => { runMaxAttempts = Math.max(1, parseInt(e.target.value, 10) || 1); },
    });
    return el("div", { class: "panel" },
      el("h3", {}, "Run Config"),
      el("p", { class: "field-help" },
        "Agent and sandbox used for runs started below (files or the builder). " +
        "Pick an agent — without one the workflow runs with no agent and its oracle fails. " +
        "Max attempts re-runs each stage on oracle fail/error/timeout (1 = no retry)."),
      el("div", { class: "row" },
        el("div", {}, el("label", {}, "Agent"), agentSel),
        el("div", {}, el("label", {}, "Sandbox"), sandboxSel),
        el("div", {}, el("label", {}, "Max attempts"), attemptsInput)));
  }

  // --- Files panel ----------------------------------------------------------
  // The folder (relative to workflows/) a workflow file lives in, derived from its
  // path: "workflows/suite/foo.yaml" -> "suite", "workflows/foo.yaml" -> "".
  function folderOfPath(path) {
    const p = String(path || "").replace(/^workflows\//, "");
    const i = p.lastIndexOf("/");
    return i >= 0 ? p.slice(0, i) : "";
  }
  // Return to the Saved-Workflows list with a specific folder open (used by the
  // back arrow / breadcrumb so leaving a suite/ workflow lands back in suite/).
  function goList(folder) {
    pendingFolder = folder || "";
    render();
    // Land on the Saved Workflows block (not the top of the page), so returning
    // from a workflow drops you back at the list you came from.
    requestAnimationFrame(() => {
      const p = document.getElementById("wf-saved-panel");
      if (p) p.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
  // Breadcrumb ancestors for a workflow in *folder*: "Workflows" (root) plus one
  // clickable crumb per path segment, each opening the list at that folder.
  function folderCrumbs(folder) {
    const crumbs = [{ label: "Workflows", onClick: () => goList("") }];
    let acc = "";
    (folder ? folder.split("/") : []).forEach((seg) => {
      acc = acc ? acc + "/" + seg : seg;
      const f = acc;
      crumbs.push({ label: seg, onClick: () => goList(f) });
    });
    return crumbs;
  }

  function filesPanel() {
    wfFolder = pendingFolder;   // open the folder requested by back-nav (else root)
    pendingFolder = "";
    const panel = el("div", { class: "panel", id: "wf-saved-panel" });
    panel.appendChild(el("h3", {}, "Saved Workflows"));
    panel.appendChild(el("p", { class: "field-help" },
      "Every workflow under the workflows/ folder. Open a 📁 folder (e.g. short/) " +
      "to see what's inside, or tick its box to select all workflows in it. Click a " +
      "name to view and customize it, Run to execute one, or check several and Run " +
      "selected. Search matches loosely across all folders."));
    // Search box: filters the list by workflow name, id, or folder.
    const search = el("input", {
      type: "search", id: "wf-files-search", placeholder: "Search workflows…",
      value: wfFilter, autocomplete: "off",
      onInput: (e) => { wfFilter = e.target.value.trim().toLowerCase(); renderFiles(); },
    });
    panel.appendChild(el("div", { class: "toolbar" }, search));
    const selectAll = el("input", {
      type: "checkbox", title: "Select all",
      onChange: (e) => toggleAll(e.target.checked),
    });
    // Split header/body tables so the scrollbar starts below the dir bar: the
    // header table (column "information bar") + the dir bar stay fixed; only the
    // body table scrolls. An identical <colgroup> on both keeps columns aligned.
    const cols = () => el("colgroup", {},
      el("col", { style: "width:6%" }), el("col", { style: "width:47%" }),
      el("col", { style: "width:8%" }), el("col", { style: "width:15%" }),
      el("col", { style: "width:12%" }), el("col", { style: "width:12%" }));
    const headTbl = el("table", { class: "wf-files-table" }, cols(),
      el("thead", {}, el("tr", {},
        el("th", {}, selectAll), el("th", {}, "Name"), el("th", {}, "Stages"),
        el("th", {}, "Prompt mode"), el("th", {}, "Status"), el("th", {}, ""))));
    const body = el("tbody", { id: "wf-files-body" });
    const bodyTbl = el("table", { class: "wf-files-table" }, cols(), body);
    panel.appendChild(el("div", { class: "toolbar" },
      el("button", { class: "btn", onClick: runSelected }, "Run selected")));
    panel.appendChild(el("div", { class: "list-frame" },
      // Header wrapped so it can reserve the same scrollbar gutter as the body --
      // otherwise the header bar is wider than the scrolling rows.
      el("div", { class: "list-head" }, headTbl),
      // Current-folder bar, merged under the header; shown only inside a folder.
      el("div", { id: "wf-crumb-bar", class: "dir-bar", style: "display:none" }),
      el("div", { class: "list-body wf-files-scroll" }, bodyTbl)));
    // Defer until the panel is in the DOM -- loadFiles looks the tbody up by id,
    // which fails if called before this panel is appended (same pattern the
    // Jobs panel uses).
    setTimeout(loadFiles, 0);
    return panel;
  }

  function loadFiles() {
    const body = document.getElementById("wf-files-body");
    if (!body) return;
    clear(body);
    body.appendChild(el("tr", {}, el("td", { colspan: "6", class: "muted" }, "Loading…")));
    api.get("/api/workflows").then((files) => {
      allFiles = files || [];
      renderFiles();
      // Fade the loaded list in (the view shell already faded while fetching).
      KARMA.replayEnter(body, "fadeIn 0.3s ease both");
    }).catch((e) => { clear(body); body.appendChild(el("tr", {}, el("td", { colspan: "6" }, errBox(e)))); });
  }

  // One row for a workflow file. When *showDir* (search results), the folder
  // path is shown above the name so cross-folder hits are distinguishable.
  function fileRow(f, showDir) {
    const status = f.ok
      ? el("span", { class: "badge ok" }, "OK")
      : el("span", { class: "badge bad" }, "INVALID");
    const runBtn = el("button", {
      class: "btn", disabled: !f.ok ? "disabled" : null,
      onClick: () => runWorkflowFile(f.path),
    }, "Run");
    const cb = el("input", {
      type: "checkbox", "data-path": f.path,
      checked: selected.has(f.path) ? "checked" : null,
      disabled: !f.ok ? "disabled" : null,
      onChange: (e) => { if (e.target.checked) selected.add(f.path); else selected.delete(f.path); },
    });
    const w = KARMA.labels.workflowName(f.name);
    return el("tr", {},
      el("td", {}, cb),
      el("td", {},
        showDir ? el("div", { class: "muted wf-file-dir" }, "workflows/" + (f.dir ? f.dir + "/" : "")) : null,
        el("span", { class: "crumb-link", onClick: () => renderWorkflowDetail(f.name, f.path) },
          w.app + (w.name ? " · " + w.name : ""))),
      el("td", {}, String(f.stage_count == null ? "—" : f.stage_count)),
      el("td", {}, f.prompt_mode ? KARMA.labels.promptMode(f.prompt_mode) : "—"),
      el("td", {}, status),
      el("td", {}, runBtn));
  }

  // Loose, symbol-insensitive match: every whitespace token of the query must
  // appear somewhere in the file's display name / path / id / folder. So
  // "rabbitmq blue" matches "RabbitMQ · Blue Green Migration 30 Stage …".
  function fileMatches(f, tokens) {
    if (!tokens.length) return true;
    const w = KARMA.labels.workflowName(f.name);
    const hay = `${w.app} ${w.name} ${f.name} ${f.id || ""} ${f.dir || ""}`.toLowerCase();
    return tokens.every((t) => hay.includes(t));
  }

  // Immediate subfolders directly under *folder* (one path segment deeper).
  function subfolders(folder) {
    const prefix = folder ? folder + "/" : "";
    const subs = new Set();
    for (const f of allFiles) {
      const d = f.dir || "";
      if (!d) continue;
      if (folder ? (d === folder ? false : d.startsWith(prefix)) : true) {
        const rest = folder ? d.slice(prefix.length) : d;
        if (rest) subs.add(prefix + rest.split("/")[0]);
      }
    }
    return [...subs].sort();
  }
  const filesIn = (folder) => allFiles.filter((f) => (f.dir || "") === folder);
  const filesUnder = (folder) => allFiles.filter((f) => {
    const d = f.dir || "";
    return d === folder || d.startsWith(folder ? folder + "/" : "");
  });
  // Navigate into/out of a folder and fade the new list in (folder nav only --
  // not on every search keystroke, which also calls renderFiles).
  function openFolder(folder) {
    wfFolder = folder;
    renderFiles();
    KARMA.replayEnter(document.getElementById("wf-crumb-bar"), "fadeIn 0.25s ease both");
    KARMA.replayEnter(document.getElementById("wf-files-body"), "fadeIn 0.25s ease both");
  }

  // One folder row: a select-all-inside checkbox, a clickable folder icon+name
  // that drills into it, and a count of the workflows it contains.
  function folderRow(folder) {
    const under = filesUnder(folder).filter((f) => f.ok);
    const allSel = under.length && under.every((f) => selected.has(f.path));
    const cb = el("input", {
      type: "checkbox", title: "Select all workflows in this folder",
      checked: allSel ? "checked" : null,
      onChange: (e) => {
        under.forEach((f) => { if (e.target.checked) selected.add(f.path); else selected.delete(f.path); });
        renderFiles();
      },
    });
    const name = folder.split("/").pop();
    const open = () => openFolder(folder);
    // Name + count share one cell spanning the data columns, so the count never
    // gets squeezed into a narrow column and wraps.
    return el("tr", { class: "wf-folder-row" },
      el("td", {}, cb),
      el("td", { colspan: "4" },
        el("span", { class: "crumb-link wf-folder-link", onClick: open },
          el("span", { class: "wf-folder-icon" }, "📁"), name),
        el("span", { class: "muted wf-folder-count" }, `${filesUnder(folder).length} workflows`)),
      el("td", {}, el("button", { class: "btn secondary", onClick: open }, "Open")));
  }

  // Fill (or hide) the current-folder bar under the header. While inside a folder
  // the frame gets `.has-crumb`, which merges the header and this bar into one
  // block (drops the seam between them). Clickable ancestors step back up.
  function renderCrumbBar() {
    const bar = document.getElementById("wf-crumb-bar");
    if (!bar) return;
    const frame = bar.parentElement;
    clear(bar);
    if (!wfFolder) {
      bar.style.display = "none";
      if (frame) frame.classList.remove("has-crumb");
      return;
    }
    if (frame) frame.classList.add("has-crumb");
    const parent = wfFolder.includes("/") ? wfFolder.slice(0, wfFolder.lastIndexOf("/")) : "";
    const go = (folder) => () => openFolder(folder);
    bar.appendChild(el("span", { class: "crumb-link dir-up", title: "Up one folder", onClick: go(parent) }, "←"));
    bar.appendChild(el("span", { class: "crumb-link", onClick: go("") }, "workflows"));
    let acc = "";
    wfFolder.split("/").forEach((seg, i, segs) => {
      acc = acc ? acc + "/" + seg : seg;
      bar.appendChild(el("span", { class: "crumb-sep" }, "/"));
      bar.appendChild(i === segs.length - 1
        ? el("span", { class: "wf-crumb-current" }, seg)
        : el("span", { class: "crumb-link", onClick: go(acc) }, seg));
    });
    bar.style.display = "";
  }

  // Render the list. With a search term, show a flat loose-matched result across
  // every folder. Otherwise browse the current folder: subfolders (drill in) +
  // the workflow files in it, with the current-folder bar merged under the header.
  function renderFiles() {
    const body = document.getElementById("wf-files-body");
    if (!body) return;
    clear(body);
    if (!allFiles.length) {
      renderCrumbBar();
      body.appendChild(el("tr", {}, el("td", { colspan: "6", class: "muted" }, "No workflow files found.")));
      return;
    }
    const tokens = wfFilter.split(/\s+/).filter(Boolean);
    if (tokens.length) {
      const bar = document.getElementById("wf-crumb-bar");   // searching is cross-folder
      if (bar) { clear(bar); bar.style.display = "none"; if (bar.parentElement) bar.parentElement.classList.remove("has-crumb"); }
      const hits = allFiles.filter((f) => fileMatches(f, tokens));
      if (!hits.length) {
        body.appendChild(el("tr", {}, el("td", { colspan: "6", class: "muted" }, "No workflows match your search.")));
        return;
      }
      for (const f of hits) body.appendChild(fileRow(f, true));
      return;
    }
    renderCrumbBar();
    for (const sub of subfolders(wfFolder)) body.appendChild(folderRow(sub));
    for (const f of filesIn(wfFolder)) body.appendChild(fileRow(f));
  }

  // Check/uncheck every (enabled) row and sync the `selected` set.
  function toggleAll(checked) {
    const body = document.getElementById("wf-files-body");
    if (!body) return;
    body.querySelectorAll("input[data-path]").forEach((cb) => {
      if (cb.disabled) return;
      cb.checked = checked;
      const p = cb.getAttribute("data-path");
      if (checked) selected.add(p); else selected.delete(p);
    });
  }

  // Run every checked workflow one after another -- concurrent runs would
  // contend for the single cluster. Each streams into the Jobs log; the next
  // starts only after the previous run_complete (which fires post-cleanup).
  async function runSelected() {
    const paths = [...selected];
    if (!paths.length) { KARMA.toast("Check at least one workflow first.", "error"); return; }
    const log = document.getElementById("wf-jobs-log");
    const panel = document.getElementById("wf-jobs-panel");
    if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
    if (log) log.textContent = `Running ${paths.length} workflow(s) in sequence…\n`;
    KARMA.toast(`Running ${paths.length} workflow(s) in sequence`, "info");
    let done = 0;
    for (let i = 0; i < paths.length; i++) {
      const path = paths[i];
      if (log) log.textContent += `\n[${i + 1}/${paths.length}] ${path}\n`;
      try {
        const { run_id } = await api.post("/api/run", {
          workflow_path: path, agent: runAgent || null, sandbox: runSandbox, max_attempts: runMaxAttempts,
        });
        if (log) log.textContent += `  started ${run_id}\n`;
        await streamToCompletion(run_id, log);
        done++;
      } catch (e) {
        if (log) log.textContent += `  error: ${e.message}\n`;
      }
    }
    if (log) log.textContent += `\n=== finished ${done}/${paths.length} ===\n`;
    KARMA.toast(`Finished ${done}/${paths.length} run(s)`, done === paths.length ? "success" : "error");
    refreshJobs();
  }

  // Resolve once the run reaches run_complete (or the stream ends).
  function streamToCompletion(runId, log) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => { if (!settled) { settled = true; resolve(); } };
      api.stream(`/api/run/${runId}/stream`, {
        statusPath: `/api/run/${runId}/status`,
        onEvent: (ev) => {
          if (ev.type === "progress") log.textContent += `  ${ev.message}\n`;
          else if (ev.type === "stage_complete") log.textContent += `  stage ${(ev.stage || {}).stage_id}: ${(ev.stage || {}).status}\n`;
          else if (ev.type === "run_complete") { log.textContent += `  run complete: ${ev.status}\n`; finish(); }
          if (log) log.scrollTop = log.scrollHeight;
        },
        onDone: finish,
      });
    });
  }

  // Saved-workflow detail: read-only stages + run + "customize" (load into the
  // builder to override params and save a renamed copy).
  async function renderWorkflowDetail(name, path) {
    clear(root);
    KARMA.replayEnter(root);
    KARMA.currentLocation = () => KARMA.showWorkflow(name, path);
    const wn = KARMA.labels.workflowName(name);
    const display = wn.app + (wn.name ? " · " + wn.name : "");
    const folder = folderOfPath(path);
    KARMA.setBreadcrumb({ back: () => goList(folder), crumbs: folderCrumbs(folder).concat([{ label: display }]) });
    root.appendChild(el("h2", {}, display));
    let wf;
    try { wf = await api.get(`/api/workflows/${name}`); }
    catch (e) { root.appendChild(errBox(e)); return; }

    // Status badges directly under the heading (same style as the case detail).
    const badges = el("div", { class: "toolbar" });
    badges.appendChild(el("span", { class: "badge" }, `${(wf.stages || []).length} stages`));
    if (wf.prompt_mode) badges.appendChild(el("span", { class: "badge" }, KARMA.labels.promptMode(wf.prompt_mode)));
    root.appendChild(badges);

    root.appendChild(runConfigPanel());

    root.appendChild(el("div", { class: "toolbar" },
      el("button", { class: "btn", onClick: () => runWorkflowFile(path) }, "Run"),
      el("button", { class: "btn secondary", onClick: () => customizeInBuilder(name, wf, path) }, "Customize / duplicate")));

    root.appendChild(KARMA.workflowStagesPanel(wf, "Stages", (s) => KARMA.showCase(s.service, s.case_name)));
    root.appendChild(jobsPanel());   // so Run output shows on this page too
  }

  // Load a saved workflow into the builder so the user can override params and
  // save it under a new name (a customized copy). Renders a dedicated builder
  // page (not the full Workflows list) so the user edits and runs in one place.
  function customizeInBuilder(name, wf, path) {
    stages = (wf.stages || []).map((s) => ({
      service: s.service, case: s.case_name,
      overrides: { ...(s.param_overrides || {}) }, _defaults: {},
    }));
    // Load the workflow's existing adversary injections (map stage ids -> idx),
    // carrying their param overrides.
    const sidx = {};
    (wf.stages || []).forEach((s, i) => { sidx[s.id] = i; });
    advRows = (wf.adversary || []).map((a) => ({
      scenario: a.scenario,
      injectIndex: sidx[a.inject_at_stage] != null ? sidx[a.inject_at_stage] : 0,
      liftIndex: sidx[a.lift_at_stage] != null ? sidx[a.lift_at_stage] : 0,
      overrides: { ...(a.param_overrides || {}) },
    }));
    builderId = name.replace(/\.ya?ml$/i, "") + "-copy";
    renderCustomize(name, path);
    KARMA.toast("Loaded into the builder — edit, then Run or Save as a copy.", "info");
  }

  // Dedicated customize page: heading + run config + builder + run output only.
  // No "Saved Workflows" list (that belongs on the Workflows landing page).
  function renderCustomize(name, path) {
    clear(root);
    KARMA.replayEnter(root);
    const wn = KARMA.labels.workflowName(name);
    const display = wn.app + (wn.name ? " · " + wn.name : "");
    KARMA.currentLocation = () => renderCustomize(name, path);
    KARMA.setBreadcrumb({ back: () => renderWorkflowDetail(name, path), crumbs: folderCrumbs(folderOfPath(path)).concat([
      { label: display, onClick: () => renderWorkflowDetail(name, path) },
      { label: "Customize" },
    ]) });
    root.appendChild(el("h2", {}, "Customize workflow"));
    root.appendChild(el("p", { class: "field-help" },
      "Editing a copy of " + display + ". Adjust stages, parameters, and adversary " +
      "injections below, then Generate YAML and Run inline to run it with the agent " +
      "selected above, or Save to workflows to keep it as a new file."));
    root.appendChild(runConfigPanel());
    root.appendChild(builderPanel());
    root.appendChild(jobsPanel());
    setTimeout(() => {
      const b = document.getElementById("wf-builder");
      if (b) b.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  }

  async function runWorkflowFile(path) {
    const out = document.getElementById("wf-jobs-log");
    if (out) out.textContent = `Submitting ${path}…\n`;
    try {
      const { run_id } = await api.post("/api/run", {
        workflow_path: path, agent: runAgent || null, sandbox: runSandbox, max_attempts: runMaxAttempts,
      });
      KARMA.toast("Workflow started: " + run_id, "info");
      streamInto(run_id);
    } catch (e) {
      if (out) out.textContent += "Error: " + e.message + "\n";
      KARMA.toastError(e);
    }
  }

  // --- Builder panel --------------------------------------------------------
  function builderPanel() {
    const panel = el("div", { class: "panel", id: "wf-builder" });

    const idInput = el("input", { value: builderId });
    // Agent session: persistent (default) keeps ONE agent conversation across all
    // stages; per_stage starts a fresh agent each stage.
    const sessionSel = el("select", {},
      el("option", { value: "persistent" }, "Persistent — one agent, all stages"),
      el("option", { value: "per_stage" }, "Per stage — fresh agent each stage"));
    const modeSel = el("select", {},
      el("option", { value: "progressive" }, KARMA.labels.promptMode("progressive")),
      el("option", { value: "concat_stateful" }, KARMA.labels.promptMode("concat_stateful")),
      el("option", { value: "concat_blind" }, KARMA.labels.promptMode("concat_blind")));
    const top = el("div", { class: "row" },
      el("div", {}, el("label", {}, "Workflow ID"), idInput),
      el("div", {}, el("label", {}, "Agent Session"), sessionSel),
      el("div", {}, el("label", {}, "Prompt Mode"), modeSel));
    panel.appendChild(el("h3", {}, "Basics"));
    panel.appendChild(el("p", { class: "field-help" },
      "Workflow ID is a short name for this workflow. Agent session controls whether " +
      "one agent runs the whole workflow (Persistent — the same conversation resumes " +
      "each stage) or a fresh agent runs each stage (Per stage). Prompt mode controls " +
      "how earlier stages' prompts are shown to the agent — Progressive sends only " +
      "the current stage (a persistent agent remembers the rest), Concatenated " +
      "(stateful) sends the full history so far with each stage tagged active/earlier, " +
      "and Concatenated (blind) sends the full history untagged."));
    panel.appendChild(top);

    // Optional workflow-level system prompt, APPENDED to the default (harness
    // contract) and sent to every agent each stage.
    const sysInput = el("textarea", { rows: "4", class: "wf-system-prompt",
      placeholder: "Optional — appended to the default system prompt and sent to every "
        + "agent each stage (for experiments, e.g. \"a regression sweep will re-check "
        + "every earlier stage\"). Don't describe how to submit; the harness handles that." });
    sysInput.addEventListener("input", () => autosize(sysInput));
    panel.appendChild(el("div", { style: "margin-top:10px" },
      el("label", {}, "System Prompt (optional — appended to the default)"), sysInput));

    panel.appendChild(el("h3", {}, "Stages"));
    panel.appendChild(el("p", { class: "field-help" },
      "Each stage runs one case, in order. Pick a service and a case, then fill in the " +
      "parameters that appear below the row. To reuse a value from an earlier stage, " +
      "type ${stages.<stage-id>.params.<name>} as the parameter value."));
    const stageList = el("div", { class: "builder-list builder-stage-list" });
    panel.appendChild(stageList);

    function renderStages() {
      clear(stageList);
      stages.forEach((stage, i) => stageList.appendChild(stageRow(stage, i, renderStages)));
    }
    renderStages();

    const addBtn = el("button", { class: "btn secondary", onClick: () => {
      stages.push({ service: services[0] ? services[0].name : "", case: "", overrides: {}, _defaults: {} });
      renderStages();
      // The stage list has a capped height; scroll the just-added last row into
      // view so the new stage isn't hidden below the fold.
      const last = stageList.lastElementChild;
      if (last) last.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } }, "+ Add stage");

    // Adversary injections -- an option of the workflow, not a separate tab.
    const advList = el("div", { class: "builder-list builder-adv-list" });
    function renderAdv() {
      clear(advList);
      advRows.forEach((adv, i) => advList.appendChild(advRow(adv, i, renderAdv)));
    }
    renderAdv();
    const addAdvBtn = el("button", {
      class: "btn secondary",
      disabled: !scenarios.length ? "disabled" : null,
      onClick: () => {
        if (!stages.length) {
          KARMA.toast("Add a stage before adding an adversarial injection.", "error");
          return;
        }
        advRows.push({ scenario: "", injectIndex: 0, liftIndex: -1 });
        renderAdv();
        // Same as stages: scroll the just-added row into view so it isn't
        // hidden below the capped-height list.
        const last = advList.lastElementChild;
        if (last) last.scrollIntoView({ block: "nearest", behavior: "smooth" });
      },
    }, "+ Add adversary");
    const advHint = scenarios.length
      ? "Optional. Inject an adversarial scenario (a deliberate fault) at a stage " +
        "to test how the agent diagnoses and recovers, and optionally lift it at a " +
        "later stage."
      : "No adversarial scenarios found under adversaries/.";

    const yaml = el("textarea", { rows: "3", id: "wf-yaml", placeholder: "workflow YAML" });
    const valBtn = el("button", { class: "btn secondary", onClick: () => validateYaml(yaml.value, msg) }, "Validate");
    // Regenerate from the current builder state so edits are always reflected --
    // the user can Run inline directly without clicking Generate YAML first.
    const runBtn = el("button", { class: "btn", onClick: () => {
      const text = buildYamlOrWarn();
      if (text) { showYaml(text); runInlineYaml(text, msg); }
    } }, "Run inline");
    const msg = el("div", { class: "muted" });
    // The output (editable YAML + validate/run) is hidden until the user
    // generates it, so the page is not dominated by an empty box up front.
    const output = el("div", { style: "display:none" }, yaml,
      el("div", { class: "toolbar" }, valBtn, runBtn), msg);
    yaml.addEventListener("input", () => autosize(yaml));

    // Validate the builder state and return the generated YAML, or null
    // (after raising an error toast) when the workflow is incomplete.
    function buildYamlOrWarn() {
      if (!stages.length) { KARMA.toast("Add at least one stage first.", "error"); return null; }
      const bad = stages.findIndex((s) => !s.service || !s.case);
      if (bad >= 0) { KARMA.toast(`Stage ${bad + 1}: choose a service and a case.`, "error"); return null; }
      return generateYaml(idInput.value, sessionSel.value, modeSel.value, sysInput.value, stages, advRows);
    }
    function showYaml(text) { yaml.value = text; output.style.display = ""; autosize(yaml); }

    const genBtn = el("button", { class: "btn", onClick: () => {
      const text = buildYamlOrWarn();
      if (text) showYaml(text);
    } }, "Generate YAML");
    const saveBtn = el("button", { class: "btn secondary", onClick: async () => {
      const text = buildYamlOrWarn();
      if (!text) return;
      showYaml(text);
      try {
        const res = await api.post("/api/workflows", { yaml_text: text, name: idInput.value });
        KARMA.toast("Saved as " + res.name, "success");
        loadFiles();
      } catch (e) { KARMA.toastError(e); }
    } }, "Save to workflows");

    panel.appendChild(el("div", { class: "toolbar" }, addBtn));

    panel.appendChild(el("h3", { id: "wf-adversary" }, "Adversarial Scenario Injection"));
    panel.appendChild(el("p", { class: "field-help" }, advHint));
    panel.appendChild(advList);
    panel.appendChild(el("div", { class: "toolbar" }, addAdvBtn));

    panel.appendChild(el("h3", {}, "Generate & Run"));
    panel.appendChild(el("p", { class: "field-help" },
      "Build the workflow YAML from the stages and injections above. Generate it to " +
      "edit, validate, or run it inline; Save to workflows keeps it under " +
      "workflows/ui/ so it appears in Saved Workflows above."));
    panel.appendChild(el("div", { class: "toolbar" }, genBtn, saveBtn));
    panel.appendChild(output);
    return panel;
  }

  // Grow the YAML box to fit its content, capped by the CSS max-height.
  function autosize(ta) {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight + 2, 460) + "px";
  }

  function stageOptions(selectedIndex) {
    // Stage ids are positional: stage_1..stage_N, mirroring generateYaml.
    return stages.map((s, i) => el("option", {
      value: String(i),
      selected: i === selectedIndex ? "selected" : null,
    }, `Stage ${i + 1}${s.service ? " (" + KARMA.labels.service(s.service) + ")" : ""}`));
  }

  function advRow(adv, index, rerender) {
    // The scenario must belong to the service of its inject stage (the backend
    // resolves it under that stage's service), so filter scenarios by it.
    const injectService = (stages[adv.injectIndex] || {}).service;
    const choices = scenarios.filter((s) => !injectService || s.service === injectService);
    const scenSel = el("select", {
      onChange: (e) => { adv.scenario = e.target.value; adv.overrides = {}; rerender(); },
    },
      el("option", { value: "" }, "(scenario)"),
      ...choices.map((s) => el("option", {
        value: s.scenario, selected: s.scenario === adv.scenario ? "selected" : null,
      }, KARMA.labels.scenario(s.scenario))));
    const injectSel = el("select", {
      onChange: (e) => { adv.injectIndex = Number(e.target.value); adv.scenario = ""; rerender(); },
    }, ...stageOptions(adv.injectIndex));
    const liftSel = el("select", { onChange: (e) => { adv.liftIndex = Number(e.target.value); } },
      el("option", { value: "-1", selected: adv.liftIndex === -1 ? "selected" : null }, "(no lift)"),
      ...stageOptions(adv.liftIndex));
    const rm = el("button", {
      class: "btn secondary", title: "Remove injection", "aria-label": "Remove injection",
      onClick: () => { advRows.splice(index, 1); rerender(); },
    }, "✕");
    const row = el("div", { class: "builder-row" },
      el("div", { class: "builder-row-head" }, el("span", {}, `Injection ${index + 1}`), rm),
      el("div", { class: "row" },
        el("div", {}, el("label", {}, "Inject at"), injectSel),
        el("div", {}, el("label", {}, "Scenario"), scenSel),
        el("div", {}, el("label", {}, "Lift at"), liftSel)));
    // Editable parameters for the chosen scenario (prefilled with defaults).
    const sdef = scenarios.find((s) => s.scenario === adv.scenario);
    const sparams = (sdef && sdef.params) || {};
    const keys = Object.keys(sparams);
    if (keys.length) {
      adv.overrides = adv.overrides || {};
      const grid = el("div", { class: "param-grid" });
      grid.style.gridTemplateColumns = `repeat(${Math.min(keys.length, 4)}, minmax(0, 1fr))`;
      for (const k of keys) {
        const pdef = sparams[k] || {};
        const def = pdef && pdef.default != null ? String(pdef.default) : "";
        const desc = pdef && typeof pdef === "object" ? (pdef.description || "") : "";
        if (!(k in adv.overrides)) adv.overrides[k] = def;
        grid.appendChild(el("div", {},
          el("label", {}, KARMA.labels.case(k)),
          el("input", {
            value: adv.overrides[k], placeholder: def,
            onInput: (e) => { adv.overrides[k] = e.target.value; },
          }),
          desc ? el("div", { class: "field-help", style: "margin:4px 0 0" }, desc) : null));
      }
      row.appendChild(el("div", { class: "stage-params" }, el("label", {}, "Parameters"), grid));
    }
    return row;
  }

  function stageRow(stage, index, rerender) {
    function resetCase() { stage.case = ""; stage.overrides = {}; stage._defaults = {}; }
    // The case dropdown is repopulated in place when the service changes, so
    // choosing a service does not rebuild the whole stage list (which felt like
    // the page refreshing -- it dropped the open dropdown and focus).
    const caseSel = el("select", { onChange: (e) => {
      stage.case = e.target.value; stage.overrides = {}; stage._defaults = {}; loadParams();
    } });
    function fillCases() {
      clear(caseSel);
      caseSel.appendChild(el("option", { value: "" }, "(case)"));
      const svc = services.find((s) => s.name === stage.service);
      for (const c of (svc ? svc.cases : [])) {
        caseSel.appendChild(el("option", {
          value: c, selected: c === stage.case ? "selected" : null,
        }, KARMA.labels.case(c)));
      }
    }
    const svcSel = el("select", { onChange: (e) => {
      stage.service = e.target.value; resetCase(); fillCases(); note("Please choose a service and a case.");
    } },
      ...services.map((s) => el("option", { value: s.name, selected: s.name === stage.service ? "selected" : null }, KARMA.labels.service(s.name))));
    fillCases();
    const rm = el("button", {
      class: "btn secondary", title: "Remove stage", "aria-label": "Remove stage",
      onClick: () => { stages.splice(index, 1); rerender(); },
    }, "✕");

    // Parameters area: a message until a case is chosen, then one labeled
    // input per declared parameter (prefilled with the default).
    const paramsBox = el("div", { class: "stage-params" });
    function note(text) {
      clear(paramsBox);
      paramsBox.appendChild(el("p", { class: "field-help", style: "margin:0" }, text));
    }
    async function loadParams() {
      if (!stage.service || !stage.case) { note("Please choose a service and a case."); return; }
      note("Loading parameters…");
      let d;
      try {
        d = await api.get(`/api/cases/${stage.service}/${stage.case}`);
      } catch (e) { note("Couldn't load parameters."); KARMA.toastError(e); return; }
      const params = d.params || [];
      stage._defaults = {};
      if (!params.length) { note("This case isn't parameterized."); return; }
      clear(paramsBox);
      paramsBox.appendChild(el("label", {}, "Parameters"));
      const grid = el("div", { class: "param-grid" });
      // Flexible: one column per parameter so a few params fill the width,
      // capped at 4 per row (more params wrap to the next row).
      grid.style.gridTemplateColumns = `repeat(${Math.min(params.length, 4)}, minmax(0, 1fr))`;
      for (const p of params) {
        const def = p.default == null ? "" : String(p.default);
        stage._defaults[p.name] = def;
        if (stage.overrides[p.name] === undefined) stage.overrides[p.name] = def;
        const input = el("input", {
          value: stage.overrides[p.name],
          onInput: (e) => { stage.overrides[p.name] = e.target.value; },
        });
        grid.appendChild(el("div", {},
          el("label", {}, KARMA.labels.case(p.name)),
          input,
          p.description ? el("div", { class: "field-help", style: "margin:4px 0 0" }, p.description) : null));
      }
      paramsBox.appendChild(grid);
    }
    loadParams();

    return el("div", { class: "builder-row" },
      el("div", { class: "builder-row-head" }, el("span", {}, `Stage ${index + 1}`), rm),
      el("div", { class: "row" },
        el("div", {}, el("label", {}, "Service"), svcSel),
        el("div", {}, el("label", {}, "Case"), caseSel)),
      paramsBox);
  }

  function generateYaml(id, session, mode, systemPrompt, stageRows, adversaryRows) {
    const lines = [`metadata:`, `  id: ${id}`, `spec:`,
                   `  agent_session: ${session}`, `  prompt_mode: ${mode}`];
    const sp = (systemPrompt || "").trim();
    if (sp) {
      lines.push(`  system_prompt: |`);
      for (const ln of sp.split("\n")) lines.push(`    ${ln}`);
    }
    lines.push(`  stages:`);
    stageRows.forEach((s, i) => {
      lines.push(`    - id: stage_${i + 1}`);
      lines.push(`      service: ${s.service}`);
      lines.push(`      case: ${s.case}`);
      // Emit only params the user changed from the case default.
      const ov = s.overrides || {};
      const defs = s._defaults || {};
      const changed = Object.keys(ov).filter((k) => ov[k] !== "" && ov[k] !== (defs[k] ?? ""));
      if (changed.length) {
        lines.push(`      param_overrides:`);
        for (const k of changed) lines.push(`        ${k}: ${ov[k]}`);
      }
    });
    const advs = (adversaryRows || []).filter((a) => a.scenario);
    if (advs.length) {
      lines.push(`  adversary:`);
      for (const a of advs) {
        lines.push(`    - scenario: ${a.scenario}`);
        lines.push(`      inject_at_stage: stage_${a.injectIndex + 1}`);
        if (a.liftIndex >= 0) {
          lines.push(`      lift_at_stage: stage_${a.liftIndex + 1}`);
        }
        // Emit only scenario params the user changed from the default.
        const sdef = scenarios.find((s) => s.scenario === a.scenario) || {};
        const sparams = sdef.params || {};
        const ov = a.overrides || {};
        const changed = Object.keys(ov).filter((k) => {
          const def = sparams[k] && sparams[k].default != null ? String(sparams[k].default) : "";
          return ov[k] !== "" && String(ov[k]) !== def;
        });
        if (changed.length) {
          lines.push(`      param_overrides:`);
          for (const k of changed) lines.push(`        ${k}: ${ov[k]}`);
        }
      }
    }
    return lines.join("\n") + "\n";
  }

  async function validateYaml(text, msg) {
    msg.className = "muted";
    msg.textContent = "Validating…";
    try {
      const res = await api.post("/api/workflow/import", { yaml_text: text });
      if (res.ok) {
        msg.className = "badge ok";
        msg.textContent = "Passed";
      } else {
        // Keep the status a clean pass/fail badge; the reasons go to the toast.
        msg.className = "badge bad";
        msg.textContent = "Failed";
        KARMA.toast((res.errors || ["Invalid workflow"]).join("; "), "error");
      }
    } catch (e) { msg.className = "badge bad"; msg.textContent = "Failed"; KARMA.toastError(e); }
  }

  async function runInlineYaml(text, msg) {
    msg.className = "muted";
    msg.textContent = "Submitting…";
    try {
      const { run_id } = await api.post("/api/run", {
        workflow_yaml: text, agent: runAgent || null, sandbox: runSandbox, max_attempts: runMaxAttempts,
      });
      msg.textContent = "Started run " + run_id;
      KARMA.toast("Workflow started: " + run_id, "info");
      streamInto(run_id);
    } catch (e) { msg.className = "badge bad"; msg.textContent = e.message; KARMA.toastError(e); }
  }

  // --- Jobs panel -----------------------------------------------------------
  function jobsPanel() {
    const panel = el("div", { class: "panel", id: "wf-jobs-panel" });
    panel.appendChild(el("h3", {}, "Jobs"));
    panel.appendChild(el("p", { class: "field-help" },
      "Runs started from this page while the server is up. Click Refresh to update."));
    panel.appendChild(el("div", { class: "toolbar" },
      el("button", { class: "btn secondary", onClick: refreshJobs }, "Refresh")));
    panel.appendChild(el("div", { id: "wf-jobs-table" }));
    panel.appendChild(el("pre", { class: "log", id: "wf-jobs-log" }, "Run output appears here.\n"));
    panel.appendChild(el("div", { id: "wf-jobs-detail" }));
    setTimeout(refreshJobs, 0);
    return panel;
  }

  async function refreshJobs() {
    const host = document.getElementById("wf-jobs-table");
    if (!host) return;
    try {
      const jobs = await api.get("/api/jobs");
      const tbl = el("table", {}, el("thead", {}, el("tr", {},
        el("th", {}, "Run ID"), el("th", {}, "Kind"), el("th", {}, "Status"))));
      const body = el("tbody", {});
      for (const j of jobs) {
        const st = KARMA.labels.status(j.status);
        body.appendChild(el("tr", {},
          el("td", {}, j.run_id || "—"),
          el("td", {}, KARMA.humanize(j.kind) || "—"),
          el("td", {}, el("span", { class: "badge " + st.cls }, st.text))));
      }
      if (!jobs.length) body.appendChild(el("tr", {}, el("td", { colspan: "3", class: "muted" }, "No active jobs.")));
      tbl.appendChild(body);
      // Clear + append AFTER the fetch so two concurrent refreshes (e.g.
      // run_complete + onDone) don't each append a table -> duplicate rows.
      clear(host);
      host.appendChild(tbl);
    } catch (e) { clear(host); host.appendChild(errBox(e)); }
  }

  function streamInto(runId) {
    const log = document.getElementById("wf-jobs-log");
    if (!log) return;
    // Bring the Jobs section into view so the live run output is visible at once.
    const panel = document.getElementById("wf-jobs-panel");
    if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
    const detail = document.getElementById("wf-jobs-detail");
    if (detail) clear(detail);
    log.textContent = `Streaming ${runId}…\n`;
    api.stream(`/api/run/${runId}/stream`, {
      statusPath: `/api/run/${runId}/status`,
      onEvent: (ev) => {
        if (ev.type === "progress") {
          log.textContent += `  ${ev.message}\n`;
        } else if (ev.type === "stage_complete") {
          const s = ev.stage || {};
          log.textContent += `stage ${s.stage_id}: ${s.status}\n`;
          if (s.status !== "pass" && detail) detail.appendChild(KARMA.stageDetail(runId, s));
        } else if (ev.type === "run_complete") {
          log.textContent += `run complete: ${ev.status}\n`;
          KARMA.toast("Run " + KARMA.labels.status(ev.status).text.toLowerCase(),
            ev.status === "complete" ? "success" : "error");
          refreshJobs();
        }
        log.scrollTop = log.scrollHeight;
      },
      onDone: () => { log.textContent += "— stream ended —\n"; refreshJobs(); },
    });
  }

  KARMA.registerView({ id: "workflow", label: "Workflow", mount });
})();

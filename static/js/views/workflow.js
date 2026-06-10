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
  let services = [];
  let agents = [];      // available agent names, for the run-config selectors
  let runAgent = "";    // agent applied to workflow runs ("" = no agent)
  let runSandbox = "local";
  let builderId = "ui-workflow";  // default name for builder saves (set when customizing)
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
    render();
  }

  function render() {
    clear(root);
    root.appendChild(el("h2", {}, "Workflows"));
    root.appendChild(runConfigPanel());
    root.appendChild(filesPanel());
    root.appendChild(builderPanel());
    root.appendChild(jobsPanel());
  }

  // Agent + sandbox applied to every workflow run started from this page.
  // Without this, workflow runs went out with no agent and always failed.
  function runConfigPanel() {
    const agentSel = el("select", { onChange: (e) => { runAgent = e.target.value; } },
      el("option", { value: "" }, "None — run locally"),
      ...agents.map((a) => el("option", {
        value: a, selected: a === runAgent ? "selected" : null,
      }, KARMA.labels.agent(a))));
    const sandboxSel = el("select", { onChange: (e) => { runSandbox = e.target.value; } },
      el("option", { value: "local", selected: runSandbox === "local" ? "selected" : null }, "Local"),
      el("option", { value: "docker", selected: runSandbox === "docker" ? "selected" : null }, "Docker"));
    return el("div", { class: "panel" },
      el("h3", {}, "Run Config"),
      el("p", { class: "field-help" },
        "Agent and sandbox used for runs started below (files or the builder). " +
        "Pick an agent — without one the workflow runs with no agent and its oracle fails."),
      el("div", { class: "row" },
        el("div", {}, el("label", {}, "Agent"), agentSel),
        el("div", {}, el("label", {}, "Sandbox"), sandboxSel)));
  }

  // --- Files panel ----------------------------------------------------------
  function filesPanel() {
    const panel = el("div", { class: "panel" });
    panel.appendChild(el("h3", {}, "Saved Workflows"));
    panel.appendChild(el("p", { class: "field-help" },
      "Predefined workflows from the workflows/ folder, plus any you save from the " +
      "builder below (kept under workflows/ui/). Click a name to view and customize " +
      "it, or Run to execute one."));
    const tbl = el("table", {}, el("thead", {}, el("tr", {},
      el("th", {}, "File"), el("th", {}, "ID"), el("th", {}, "Stages"),
      el("th", {}, "Prompt mode"), el("th", {}, "Status"), el("th", {}, ""))));
    const body = el("tbody", { id: "wf-files-body" });
    tbl.appendChild(body);
    panel.appendChild(el("div", { class: "scroll-list" }, tbl));
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
    api.get("/api/workflows").then((files) => {
      if (!files.length) body.appendChild(el("tr", {}, el("td", { colspan: "6", class: "muted" }, "No workflow files found.")));
      for (const f of files) {
        const status = f.ok
          ? el("span", { class: "badge ok" }, "OK")
          : el("span", { class: "badge bad" }, "INVALID");
        const runBtn = el("button", {
          class: "btn", disabled: !f.ok ? "disabled" : null,
          onClick: () => runWorkflowFile(f.path),
        }, "Run");
        body.appendChild(el("tr", {},
          el("td", {}, el("span", { class: "crumb-link", onClick: () => renderWorkflowDetail(f.name, f.path) }, f.name.replace(/\.ya?ml$/i, ""))),
          el("td", {}, f.id || "—"),
          el("td", {}, String(f.stage_count == null ? "—" : f.stage_count)),
          el("td", {}, f.prompt_mode ? KARMA.labels.promptMode(f.prompt_mode) : "—"), el("td", {}, status),
          el("td", {}, runBtn)));
      }
    }).catch((e) => body.appendChild(el("tr", {}, el("td", { colspan: "6" }, errBox(e)))));
  }

  // Saved-workflow detail: read-only stages + run + "customize" (load into the
  // builder to override params and save a renamed copy).
  async function renderWorkflowDetail(name, path) {
    clear(root);
    const display = name.replace(/\.ya?ml$/i, "");   // drop the .yaml suffix
    KARMA.setBreadcrumb({ back: render, crumbs: [{ label: "Workflows", onClick: render }, { label: display }] });
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
      el("button", { class: "btn secondary", onClick: () => customizeInBuilder(name, wf) }, "Customize / duplicate")));

    root.appendChild(KARMA.workflowStagesPanel(wf, "Stages"));
    root.appendChild(jobsPanel());   // so Run output shows on this page too
  }

  // Load a saved workflow into the builder so the user can override params and
  // save it under a new name (a customized copy).
  function customizeInBuilder(name, wf) {
    stages = (wf.stages || []).map((s) => ({
      service: s.service, case: s.case_name,
      overrides: { ...(s.param_overrides || {}) }, _defaults: {},
    }));
    advRows = [];
    builderId = name.replace(/\.ya?ml$/i, "") + "-copy";
    render();
    setTimeout(() => {
      const b = document.getElementById("wf-builder");
      if (b) b.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
    KARMA.toast("Loaded into builder — edit params and Save to workflows as a copy", "info");
  }

  async function runWorkflowFile(path) {
    const out = document.getElementById("wf-jobs-log");
    if (out) out.textContent = `Submitting ${path}…\n`;
    try {
      const { run_id } = await api.post("/api/run", {
        workflow_path: path, agent: runAgent || null, sandbox: runSandbox,
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
    const modeSel = el("select", {},
      el("option", { value: "progressive" }, KARMA.labels.promptMode("progressive")),
      el("option", { value: "concat_stateful" }, KARMA.labels.promptMode("concat_stateful")),
      el("option", { value: "concat_blind" }, KARMA.labels.promptMode("concat_blind")));
    const top = el("div", { class: "row" },
      el("div", {}, el("label", {}, "Workflow ID"), idInput),
      el("div", {}, el("label", {}, "Prompt Mode"), modeSel));
    panel.appendChild(el("h3", {}, "Basics"));
    panel.appendChild(el("p", { class: "field-help" },
      "Workflow ID is a short name for this workflow. Prompt mode controls how " +
      "earlier stages' prompts are shown to the agent — Progressive adds each " +
      "stage to the previous, Concatenated (stateful) shows the full running " +
      "history, and Concatenated (blind) shows only the current stage."));
    panel.appendChild(top);

    panel.appendChild(el("h3", {}, "Stages"));
    panel.appendChild(el("p", { class: "field-help" },
      "Each stage runs one case, in order. Pick a service and a case, then fill in the " +
      "parameters that appear below the row. To reuse a value from an earlier stage, " +
      "type ${stages.<stage-id>.params.<name>} as the parameter value."));
    const stageList = el("div", { class: "builder-list" });
    panel.appendChild(stageList);

    function renderStages() {
      clear(stageList);
      stages.forEach((stage, i) => stageList.appendChild(stageRow(stage, i, renderStages)));
    }
    renderStages();

    const addBtn = el("button", { class: "btn secondary", onClick: () => {
      stages.push({ service: services[0] ? services[0].name : "", case: "", overrides: {}, _defaults: {} });
      renderStages();
    } }, "+ Add stage");

    // Adversary injections -- an option of the workflow, not a separate tab.
    const advList = el("div", { class: "builder-list" });
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
      },
    }, "+ Add adversary");
    const advHint = scenarios.length
      ? "Optional. Inject an adversarial scenario (a deliberate fault) at a stage " +
        "to test how the agent diagnoses and recovers, and optionally lift it at a " +
        "later stage."
      : "No adversarial scenarios found under resources/*/adversarial/.";

    const yaml = el("textarea", { rows: "3", id: "wf-yaml", placeholder: "workflow YAML" });
    const valBtn = el("button", { class: "btn secondary", onClick: () => validateYaml(yaml.value, msg) }, "Validate");
    const runBtn = el("button", { class: "btn", onClick: () => runInlineYaml(yaml.value, msg) }, "Run inline");
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
      return generateYaml(idInput.value, modeSel.value, stages, advRows);
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

    panel.appendChild(el("h3", {}, "Adversarial Scenario Injection"));
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
    const scenSel = el("select", { onChange: (e) => { adv.scenario = e.target.value; } },
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
    return el("div", { class: "builder-row" },
      el("div", { class: "builder-row-head" }, el("span", {}, `Injection ${index + 1}`), rm),
      el("div", { class: "row" },
        el("div", {}, el("label", {}, "Inject at"), injectSel),
        el("div", {}, el("label", {}, "Scenario"), scenSel),
        el("div", {}, el("label", {}, "Lift at"), liftSel)));
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

  function generateYaml(id, mode, stageRows, adversaryRows) {
    const lines = [`metadata:`, `  id: ${id}`, `spec:`, `  prompt_mode: ${mode}`, `  stages:`];
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
        msg.textContent = `Valid: ${res.workflow.stages.length} stage(s), id=${res.workflow.id}`;
      } else {
        msg.className = "badge bad";
        msg.textContent = (res.errors || []).join("; ");
        KARMA.toast((res.errors || ["Invalid workflow"]).join("; "), "error");
      }
    } catch (e) { msg.className = "badge bad"; msg.textContent = e.message; KARMA.toastError(e); }
  }

  async function runInlineYaml(text, msg) {
    msg.className = "muted";
    msg.textContent = "Submitting…";
    try {
      const { run_id } = await api.post("/api/run", {
        workflow_yaml: text, agent: runAgent || null, sandbox: runSandbox,
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

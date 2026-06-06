/*
 * KARMA web UI -- Workflow view.
 *
 * Three panels:
 *   - Files: the workflow YAML files on disk (/api/workflows), each runnable.
 *   - Builder: add stages (service + case + param overrides) and a prompt
 *     mode, generate YAML, validate it via /api/workflow/import, and run it
 *     inline through /api/run with a workflow_yaml payload.
 *   - Jobs: the active job list (/api/jobs) plus a live log for a started run.
 */
(function () {
  "use strict";
  const KARMA = window.KARMA;
  const { el, clear, api } = KARMA;

  let root;
  let services = [];
  let stages = [];   // builder stage rows: {service, case, overrides}

  function errBox(e) { return el("div", { class: "error-box" }, e.message || String(e)); }

  async function mount(container) {
    root = container;
    stages = [];   // start each mount with a fresh builder
    if (!services.length) {
      try { services = (await api.get("/api/services")).services || []; } catch (_e) { services = []; }
    }
    render();
  }

  function render() {
    clear(root);
    root.appendChild(el("h2", {}, "Workflows"));
    root.appendChild(filesPanel());
    root.appendChild(builderPanel());
    root.appendChild(jobsPanel());
  }

  // --- Files panel ----------------------------------------------------------
  function filesPanel() {
    const panel = el("div", { class: "panel" });
    panel.appendChild(el("h3", {}, "Workflow files"));
    const tbl = el("table", {}, el("thead", {}, el("tr", {},
      el("th", {}, "File"), el("th", {}, "ID"), el("th", {}, "Stages"),
      el("th", {}, "Prompt mode"), el("th", {}, "Status"), el("th", {}, ""))));
    const body = el("tbody", {});
    tbl.appendChild(body);
    panel.appendChild(tbl);
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
          el("td", {}, f.name), el("td", {}, f.id || "—"),
          el("td", {}, String(f.stage_count == null ? "—" : f.stage_count)),
          el("td", {}, f.prompt_mode || "—"), el("td", {}, status),
          el("td", {}, runBtn)));
      }
    }).catch((e) => body.appendChild(el("tr", {}, el("td", { colspan: "6" }, errBox(e)))));
    return panel;
  }

  async function runWorkflowFile(path) {
    const out = document.getElementById("wf-jobs-log");
    if (out) out.textContent = `Submitting ${path}…\n`;
    try {
      const { run_id } = await api.post("/api/run", { workflow_path: path });
      streamInto(run_id);
    } catch (e) {
      if (out) out.textContent += "Error: " + e.message + "\n";
    }
  }

  // --- Builder panel --------------------------------------------------------
  function builderPanel() {
    const panel = el("div", { class: "panel" });
    panel.appendChild(el("h3", {}, "Builder"));

    const idInput = el("input", { value: "ui-workflow" });
    const modeSel = el("select", {},
      el("option", { value: "progressive" }, "progressive"),
      el("option", { value: "concat_stateful" }, "concat_stateful"),
      el("option", { value: "concat_blind" }, "concat_blind"));
    const top = el("div", { class: "row" },
      el("div", {}, el("label", {}, "Workflow id"), idInput),
      el("div", {}, el("label", {}, "Prompt mode"), modeSel));
    panel.appendChild(top);

    const stageList = el("div", {});
    panel.appendChild(stageList);

    function renderStages() {
      clear(stageList);
      stages.forEach((stage, i) => stageList.appendChild(stageRow(stage, i, renderStages)));
    }
    renderStages();

    const addBtn = el("button", { class: "btn secondary", onClick: () => {
      stages.push({ service: services[0] ? services[0].name : "", case: "", overrides: "" });
      renderStages();
    } }, "+ Add stage");

    const yaml = el("textarea", { rows: "10", id: "wf-yaml", placeholder: "workflow YAML" });
    const genBtn = el("button", { class: "btn", onClick: () => {
      yaml.value = generateYaml(idInput.value, modeSel.value, stages);
    } }, "Generate YAML");
    const valBtn = el("button", { class: "btn secondary", onClick: () => validateYaml(yaml.value, msg) }, "Validate");
    const runBtn = el("button", { class: "btn", onClick: () => runInlineYaml(yaml.value, msg) }, "Run inline");
    const msg = el("div", { class: "muted" });

    panel.appendChild(el("div", { class: "toolbar" }, addBtn, genBtn));
    panel.appendChild(yaml);
    panel.appendChild(el("div", { class: "toolbar" }, valBtn, runBtn));
    panel.appendChild(msg);
    return panel;
  }

  function stageRow(stage, index, rerender) {
    const svcSel = el("select", { onChange: (e) => { stage.service = e.target.value; stage.case = ""; rerender(); } },
      ...services.map((s) => el("option", { value: s.name, selected: s.name === stage.service ? "selected" : null }, s.name)));
    const svc = services.find((s) => s.name === stage.service);
    const caseSel = el("select", { onChange: (e) => { stage.case = e.target.value; } },
      el("option", { value: "" }, "(case)"),
      ...((svc ? svc.cases : []).map((c) =>
        el("option", { value: c, selected: c === stage.case ? "selected" : null }, c))));
    const ovr = el("input", { value: stage.overrides, placeholder: "key=value, key2=value2",
      onInput: (e) => { stage.overrides = e.target.value; } });
    const rm = el("button", { class: "btn secondary", onClick: () => { stages.splice(index, 1); rerender(); } }, "✕");
    return el("div", { class: "row", style: "margin-top:8px" },
      el("div", {}, el("label", {}, `Stage ${index + 1} service`), svcSel),
      el("div", {}, el("label", {}, "Case"), caseSel),
      el("div", {}, el("label", {}, "Param overrides"), ovr),
      el("div", { style: "flex:0" }, el("label", {}, " "), rm));
  }

  function generateYaml(id, mode, stageRows) {
    const lines = [`metadata:`, `  id: ${id}`, `spec:`, `  prompt_mode: ${mode}`, `  stages:`];
    stageRows.forEach((s, i) => {
      lines.push(`    - id: stage_${i + 1}`);
      lines.push(`      service: ${s.service}`);
      lines.push(`      case: ${s.case}`);
      const pairs = (s.overrides || "").split(",").map((x) => x.trim()).filter(Boolean);
      if (pairs.length) {
        lines.push(`      param_overrides:`);
        for (const p of pairs) {
          const [k, ...rest] = p.split("=");
          lines.push(`        ${k.trim()}: ${rest.join("=").trim()}`);
        }
      }
    });
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
      }
    } catch (e) { msg.className = "badge bad"; msg.textContent = e.message; }
  }

  async function runInlineYaml(text, msg) {
    msg.className = "muted";
    msg.textContent = "Submitting…";
    try {
      const { run_id } = await api.post("/api/run", { workflow_yaml: text });
      msg.textContent = "Started run " + run_id;
      streamInto(run_id);
    } catch (e) { msg.className = "badge bad"; msg.textContent = e.message; }
  }

  // --- Jobs panel -----------------------------------------------------------
  function jobsPanel() {
    const panel = el("div", { class: "panel" });
    panel.appendChild(el("div", { class: "toolbar" },
      el("h3", { style: "flex:1;margin:0" }, "Jobs"),
      el("button", { class: "btn secondary", onClick: refreshJobs }, "Refresh")));
    panel.appendChild(el("div", { id: "wf-jobs-table" }));
    panel.appendChild(el("pre", { class: "log", id: "wf-jobs-log" }, "Run output appears here.\n"));
    setTimeout(refreshJobs, 0);
    return panel;
  }

  async function refreshJobs() {
    const host = document.getElementById("wf-jobs-table");
    if (!host) return;
    clear(host);
    try {
      const jobs = await api.get("/api/jobs");
      const tbl = el("table", {}, el("thead", {}, el("tr", {},
        el("th", {}, "Run ID"), el("th", {}, "Kind"), el("th", {}, "Status"))));
      const body = el("tbody", {});
      for (const j of jobs) {
        body.appendChild(el("tr", {},
          el("td", {}, j.run_id || "—"),
          el("td", {}, j.kind || "—"),
          el("td", {}, el("span", { class: "badge " + badgeClass(j.status) }, j.status || "—"))));
      }
      if (!jobs.length) body.appendChild(el("tr", {}, el("td", { colspan: "3", class: "muted" }, "No active jobs.")));
      tbl.appendChild(body);
      host.appendChild(tbl);
    } catch (e) { host.appendChild(errBox(e)); }
  }

  function badgeClass(s) {
    if (s === "complete") return "ok";
    if (s === "error" || s === "failed") return "bad";
    if (s === "running") return "run";
    return "";
  }

  function streamInto(runId) {
    const log = document.getElementById("wf-jobs-log");
    if (!log) return;
    log.textContent = `Streaming ${runId}…\n`;
    api.stream(`/api/run/${runId}/stream`, {
      statusPath: `/api/run/${runId}/status`,
      onEvent: (ev) => {
        if (ev.type === "stage_complete") {
          const s = ev.stage || {};
          log.textContent += `stage ${s.stage_id}: ${s.status}\n`;
        } else if (ev.type === "run_complete") {
          log.textContent += `run complete: ${ev.status}\n`;
          refreshJobs();
        }
        log.scrollTop = log.scrollHeight;
      },
      onDone: () => { log.textContent += "— stream ended —\n"; refreshJobs(); },
    });
  }

  KARMA.registerView({ id: "workflow", label: "Workflow", mount });
})();

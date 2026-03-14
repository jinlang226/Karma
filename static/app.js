const appEl = document.getElementById("app");
const clusterEl = document.getElementById("cluster-status");
const pageTitleEl = document.getElementById("page-title");
const pageSubtitleEl = document.getElementById("page-subtitle");
const navManualEl = document.getElementById("nav-manual");
const navWorkflowEl = document.getElementById("nav-workflow");
const navJudgeEl = document.getElementById("nav-judge");

const state = {
  services: [],
  currentService: null,
  currentCases: [],
  currentCase: null,
  currentCaseDetails: null,
  runStatus: null,
  pollTimer: null,
  setupAlerted: false,
  runRendered: false,
  lastRenderedCaseId: null,
  runMetrics: null,
  lastMetricsPath: null,
  clusterOk: null,
  clusterError: null,
  proxyStatus: null,
  orchestratorOptions: null,
  commandBuilder: {
    visible: false,
    scopeType: null,
    showAdvanced: false,
    preview: null,
    flags: {},
  },
  viewMode: "manual",
  workflow: {
    subview: "builder",
    files: [],
    catalog: {},
    caseParams: {},
    handlersAttached: false,
    builder: {
      draft: {
        metadata: { name: "workflow-demo" },
        spec: {
          prompt_mode: "progressive",
          final_sweep_mode: "full",
          namespaces: [],
          stages: [],
        },
      },
      workflow_path: "workflows/my_workflow.yaml",
      yaml_preview: "",
      cli_preview: null,
      import_yaml_text: "",
      import_error: "",
      import_info: "",
      validation: { ok: false, errors: [], warnings: [] },
      dirty: false,
      last_updated_ts: null,
      expanded_stage_id: null,
      param_ref_expanded: {},
      drag: { dragging_stage_id: null, over_index: null },
    },
    runner: {
      jobs: [],
      stream: null,
      streamSeq: 0,
      streamFallback: false,
      pollTimer: null,
      pollTick: 0,
      selectedWorkflowPath: "",
      flags: {
        sandbox: "docker",
        agent: "react",
        agent_build: false,
        agent_auth_path: "",
        agent_cmd: "",
        max_attempts: "",
      },
      cliPanel: null,
      logPrefs: {},
      promptCache: {},
    },
  },
  judge: {
    runs: [],
    batches: [],
    jobs: [],
    subview: "batches",
    cliPanel: null,
    pollTimer: null,
    pollTick: 0,
    handlersAttached: false,
    stream: null,
    streamSeq: 0,
    streamFallback: false,
  },
};

function getFallbackOrchestratorOptions() {
  return {
    choices: {
      agents: ["react"],
      sandbox: ["local", "docker"],
      setup_timeout_mode: ["fixed", "auto"],
    },
    defaults: {
      agent: "react",
      agent_build: false,
      agent_tag: "",
      agent_cleanup: false,
      manual_start: false,
      llm_env_file: "",
      agent_cmd: "",
      agent_auth_path: "",
      agent_auth_dest: "",
      sandbox: "docker",
      docker_image: "",
      source_kubeconfig: "",
      proxy_server: "127.0.0.1:8081",
      real_kubectl: "",
      submit_timeout: 1200,
      setup_timeout: 600,
      setup_timeout_mode: "auto",
      verify_timeout: 1200,
      cleanup_timeout: 600,
      max_attempts: "",
      results_json: "",
    },
  };
}

function initializeCommandFlags(force = false) {
  if (Object.keys(state.commandBuilder.flags).length && !force) {
    return;
  }
  const defaults = (state.orchestratorOptions || getFallbackOrchestratorOptions()).defaults || {};
  state.commandBuilder.flags = {
    agent: defaults.agent || "react",
    agent_build: !!defaults.agent_build,
    max_attempts: defaults.max_attempts === null || defaults.max_attempts === undefined ? "" : String(defaults.max_attempts),
    agent_auth_path: defaults.agent_auth_path || "",
    agent_cmd: defaults.agent_cmd || "",
    sandbox: defaults.sandbox || "docker",
    agent_tag: defaults.agent_tag || "",
    agent_cleanup: !!defaults.agent_cleanup,
    manual_start: !!defaults.manual_start,
    llm_env_file: defaults.llm_env_file || "",
    agent_auth_dest: defaults.agent_auth_dest || "",
    docker_image: defaults.docker_image || "",
    source_kubeconfig: defaults.source_kubeconfig || "",
    proxy_server: defaults.proxy_server || "127.0.0.1:8081",
    real_kubectl: defaults.real_kubectl || "",
    submit_timeout: defaults.submit_timeout ? String(defaults.submit_timeout) : "1200",
    setup_timeout: defaults.setup_timeout ? String(defaults.setup_timeout) : "600",
    setup_timeout_mode: defaults.setup_timeout_mode || "auto",
    verify_timeout: defaults.verify_timeout ? String(defaults.verify_timeout) : "1200",
    cleanup_timeout: defaults.cleanup_timeout ? String(defaults.cleanup_timeout) : "600",
    results_json: defaults.results_json || "",
  };
}

async function fetchJSON(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Request failed (${resp.status}): ${text}`);
  }
  return resp.json();
}

function formatDuration(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function statusBadge(status) {
  const label = status.replace(/_/g, " ").toUpperCase();
  let cls = "badge";
  if (status.includes("fail")) {
    cls += " fail";
  } else if (status === "setup_failed") {
    cls += " warn";
  }
  return `<span class="${cls}">${label}</span>`;
}

function formatSetupPhase(phase) {
  if (!phase) {
    return "-";
  }
  const labels = {
    precondition_apply: "Precondition Apply",
    precondition_check: "Precondition Check",
    decoy_apply: "Decoy Apply",
    ready: "Ready",
  };
  return labels[phase] || phase.replace(/_/g, " ");
}

function renderClusterStatus(clusterOk, clusterError, proxyStatus) {
  const clusterLine = clusterOk
    ? "<strong>Cluster:</strong> connected"
    : `<strong>Cluster:</strong> error<br><span class="logs">${escapeHtml(clusterError || "")}</span>`;
  let proxyLine = "";
  if (!proxyStatus || proxyStatus.status === "disabled") {
    proxyLine = "<strong>Proxy:</strong> disabled";
  } else if (proxyStatus.status === "ok") {
    const runLabel = proxyStatus.run_id ? `run ${proxyStatus.run_id}` : "idle";
    const path = proxyStatus.log_path ? escapeHtml(proxyStatus.log_path) : "-";
    proxyLine = `<strong>Proxy:</strong> ${runLabel}<br><span class="logs">${path}</span>`;
  } else {
    const err = escapeHtml(proxyStatus.error || "unknown error");
    proxyLine = `<strong>Proxy:</strong> error<br><span class="logs">${err}</span>`;
  }
  clusterEl.innerHTML = `<div class="cluster-line">${clusterLine}</div><div class="cluster-line">${proxyLine}</div>`;
}

function updateTopNav() {
  if (state.viewMode === "judge") {
    pageTitleEl.textContent = "Judge";
    pageSubtitleEl.textContent = "Run LLM-as-Judge on single runs or full batches, or generate CLI commands.";
  } else if (state.viewMode === "workflow") {
    pageTitleEl.textContent = "Workflow";
    pageSubtitleEl.textContent = "Build workflow chains and run workflow executions with live progress.";
  } else {
    pageTitleEl.textContent = "Manual Runner";
    pageSubtitleEl.textContent = "Select a service, run setup, and verify test cases without an agent.";
  }
  navManualEl.classList.toggle("active", state.viewMode === "manual");
  if (navWorkflowEl) {
    navWorkflowEl.classList.toggle("active", state.viewMode === "workflow");
  }
  navJudgeEl.classList.toggle("active", state.viewMode === "judge");
}

function renderHome() {
  state.currentService = null;
  state.currentCases = [];
  state.currentCase = null;
  state.currentCaseDetails = null;
  state.runRendered = false;
  state.lastRenderedCaseId = null;
  state.runMetrics = null;
  state.lastMetricsPath = null;

  const cards = state.services
    .map(
      (svc) => `
      <button class="card" data-service="${svc.name}">
        <h3>${svc.label}</h3>
        <p>${svc.count} cases</p>
      </button>
    `
    )
    .join("");

  appEl.innerHTML = `
    <section class="section">
      <div class="section-title">
        <h2>Services</h2>
        <button class="button ghost" id="generate-cli-all">Generate CLI</button>
      </div>
      <div class="grid">${cards}</div>
    </section>
    ${renderCommandBuilderPanel("all")}
  `;

  appEl.querySelectorAll("button[data-service]").forEach((btn) => {
    btn.addEventListener("click", () => loadService(btn.dataset.service));
  });
  const genBtn = document.getElementById("generate-cli-all");
  if (genBtn) {
    genBtn.addEventListener("click", () => openCommandBuilder("all"));
  }
  attachCommandBuilderHandlers("all");
}

function renderCases() {
  if (!state.currentService) {
    renderHome();
    return;
  }
  state.runRendered = false;
  state.lastRenderedCaseId = null;
  state.runMetrics = null;
  state.lastMetricsPath = null;

  const caseList = state.currentCases
    .map(
      (c) => `
      <button class="card" data-case="${c.id}">
        <h3>${c.display_name}</h3>
        <p>${c.test_file}</p>
      </button>
    `
    )
    .join("");

  appEl.innerHTML = `
    <section class="section">
      <div class="section-title">
        <h2>${escapeHtml(state.currentService)}</h2>
        <button class="button ghost" id="back-home">Back</button>
        <button class="button ghost" id="generate-cli-service">Generate CLI</button>
      </div>
      <div class="grid">${caseList}</div>
    </section>
    ${renderCommandBuilderPanel("service")}
  `;

  document.getElementById("back-home").addEventListener("click", renderHome);
  appEl.querySelectorAll("button[data-case]").forEach((btn) => {
    btn.addEventListener("click", () => loadCase(btn.dataset.case));
  });
  const genBtn = document.getElementById("generate-cli-service");
  if (genBtn) {
    genBtn.addEventListener("click", () => openCommandBuilder("service"));
  }
  attachCommandBuilderHandlers("service");
}

function renderCaseDetail() {
  const c = state.currentCaseDetails;
  if (!c) {
    return;
  }
  appEl.innerHTML = `
    <section class="section">
      <div class="section-title">
        <h2>${escapeHtml(c.case)}</h2>
        <button class="button ghost" id="back-cases">Back</button>
      </div>
      <div class="meta">
        <div><span>Type</span><br>${escapeHtml(c.type || "-")}</div>
        <div><span>Target App</span><br>${escapeHtml(c.targetApp || "-")}</div>
        <div><span>Instances</span><br>${escapeHtml(String(c.numAppInstance || "-"))}</div>
        <div><span>Cluster</span><br>${escapeHtml(c.clusterType || "-")}</div>
        <div><span>Provider</span><br>${escapeHtml(c.clusterProvider || "-")}</div>
      </div>
    </section>
    <section class="section">
      <div class="section-title">
        <h2>Instructions</h2>
      </div>
      <div class="pre">${escapeHtml(c.detailedInstructions || "")}</div>
    </section>
    <section class="section">
      <div class="section-title">
        <h2>Operator Context</h2>
      </div>
      <div class="pre">${escapeHtml(c.operatorContext || "")}</div>
    </section>
    ${c.verification ? `
    <section class="section">
      <div class="section-title">
        <h2>Verification Notes</h2>
      </div>
      <div class="pre">${escapeHtml(c.verification || "")}</div>
    </section>
    ` : ""}
    <section class="section">
      <div class="section-title">
        <h2>Actions</h2>
      </div>
      <div class="status-line">
        <button class="button" id="start-run">Start</button>
        <button class="button ghost" id="generate-cli-case">Generate CLI</button>
        <span class="logs">${escapeHtml(c.path || "")}</span>
      </div>
    </section>
    ${renderCommandBuilderPanel("case")}
  `;

  document.getElementById("back-cases").addEventListener("click", renderCases);
  document.getElementById("start-run").addEventListener("click", () => startRun(c.id));
  const genBtn = document.getElementById("generate-cli-case");
  if (genBtn) {
    genBtn.addEventListener("click", () => openCommandBuilder("case"));
  }
  attachCommandBuilderHandlers("case");
}

function renderRun() {
  const s = state.runStatus;
  if (!s) {
    return;
  }
  const showInstructions = s.status !== "setup_running";
  const attempts = s.attempts ?? 0;
  const maxAttempts = s.max_attempts ?? 20;
  const elapsed = s.elapsed_seconds ?? 0;
  const timeLimit = s.time_limit_seconds ?? 0;
  const timeLeft = Math.max(0, timeLimit - elapsed);
  const caseName = s.case ? s.case.display_name : "";
  const statusBadgeHtml = statusBadge(s.status || "idle");
  const canSubmit = s.can_submit && s.status !== "auto_failed";
  const showVerificationWarning = s.status === "ready" && !s.has_verification;

  const actionsHtml = renderActionsHtml(s);
  const instructionsHtml = `
    <section class="section">
      <div class="section-title">
        <h2>Instructions</h2>
      </div>
      <div class="pre">${escapeHtml(state.currentCaseDetails?.detailedInstructions || "")}</div>
    </section>
    <section class="section">
      <div class="section-title">
        <h2>Operator Context</h2>
      </div>
      <div class="pre">${escapeHtml(state.currentCaseDetails?.operatorContext || "")}</div>
    </section>
    ${state.currentCaseDetails?.verification ? `
    <section class="section">
      <div class="section-title">
        <h2>Verification Notes</h2>
      </div>
      <div class="pre">${escapeHtml(state.currentCaseDetails?.verification || "")}</div>
    </section>
    ` : ""}
  `;

  appEl.innerHTML = `
    <section class="section">
      <div class="section-title">
        <h2 id="run-case-name">${escapeHtml(caseName)}</h2>
        <span id="run-status-badge">${statusBadgeHtml}</span>
      </div>
      <div class="status-line">
        <span id="run-attempts">Attempts: ${attempts}/${maxAttempts}</span>
        <span id="run-elapsed">Elapsed: ${formatDuration(elapsed)}</span>
        <span id="run-time-left">Time left: ${formatDuration(timeLeft)}</span>
      </div>
      <div class="logs">
        <div id="run-dir">Run folder: ${escapeHtml(s.run_dir || "-")}</div>
        <div id="run-setup-log">Setup log: ${escapeHtml(s.setup_log || "-")}</div>
        <div id="run-setup-phase">Setup phase: ${escapeHtml(formatSetupPhase(s.setup_phase))}</div>
      </div>
      <div class="logs" id="run-error"></div>
    </section>
    <section class="section">
      <div class="section-title">
        <h2>External Metrics</h2>
      </div>
      <div class="logs" id="run-metrics-path">${s.metrics_path ? `Metrics file: ${escapeHtml(s.metrics_path)}` : "Metrics file: -"}</div>
      <div class="pre" id="run-metrics-body">${escapeHtml(renderMetricsText(state.runMetrics, s))}</div>
    </section>
    <div id="run-instructions" style="display: ${showInstructions ? "block" : "none"};">
      ${instructionsHtml}
    </div>
    <section class="section">
      <div class="section-title">
        <h2>Actions</h2>
      </div>
      <div class="status-line" id="run-actions">
        ${actionsHtml}
        <span id="run-verification-badge" class="badge warn" style="display: ${showVerificationWarning ? "inline-flex" : "none"};">NO VERIFICATION</span>
      </div>
    </section>
  `;

  if (s.status === "setup_running" && state.currentCaseDetails) {
    state.setupAlerted = false;
  }

  if (s.status === "ready" && !state.setupAlerted) {
    alert("Setup finished");
    state.setupAlerted = true;
  }

  const submitBtn = document.getElementById("submit-run");
  if (submitBtn && canSubmit) {
    submitBtn.addEventListener("click", submitRun);
  }
  const backBtn = document.getElementById("back-cases");
  if (backBtn) {
    backBtn.addEventListener("click", backToCases);
  }

  state.runRendered = true;
  state.lastRenderedCaseId = s.case ? s.case.id : null;
}

function updateRunView(nextStatus, prevStatus) {
  if (!nextStatus) {
    return;
  }
  if (nextStatus.status === "passed") {
    stopPolling();
  }
  const attempts = nextStatus.attempts ?? 0;
  const maxAttempts = nextStatus.max_attempts ?? 20;
  const elapsed = nextStatus.elapsed_seconds ?? 0;
  const timeLimit = nextStatus.time_limit_seconds ?? 0;
  const timeLeft = Math.max(0, timeLimit - elapsed);
  const caseName = nextStatus.case ? nextStatus.case.display_name : "";

  const badge = document.getElementById("run-status-badge");
  if (badge) {
    badge.innerHTML = statusBadge(nextStatus.status || "idle");
  }

  setText("run-case-name", caseName);
  setText("run-attempts", `Attempts: ${attempts}/${maxAttempts}`);
  setText("run-elapsed", `Elapsed: ${formatDuration(elapsed)}`);
  setText("run-time-left", `Time left: ${formatDuration(timeLeft)}`);
  setText("run-dir", `Run folder: ${nextStatus.run_dir || "-"}`);
  setText("run-setup-log", `Setup log: ${nextStatus.setup_log || "-"}`);
  setText("run-setup-phase", `Setup phase: ${formatSetupPhase(nextStatus.setup_phase)}`);
  setText("run-metrics-path", nextStatus.metrics_path ? `Metrics file: ${nextStatus.metrics_path}` : "Metrics file: -");

  const stepEl = document.getElementById("run-current-step");
  if (stepEl) {
    if (nextStatus.setup_phase) {
      stepEl.textContent = `Setup phase: ${formatSetupPhase(nextStatus.setup_phase)} | Step: ${nextStatus.current_step || "waiting..."}`;
    } else {
      stepEl.textContent = `Setup running... ${nextStatus.current_step || ""}`;
    }
  }

  const errorEl = document.getElementById("run-error");
  if (errorEl) {
    errorEl.textContent = "";
  }

  const warnBadge = document.getElementById("run-verification-badge");
  if (warnBadge) {
    warnBadge.style.display =
      nextStatus.status === "ready" && !nextStatus.has_verification ? "inline-flex" : "none";
  }

  const instructions = document.getElementById("run-instructions");
  if (instructions) {
    instructions.style.display = nextStatus.status === "setup_running" ? "none" : "block";
  }

  const actions = document.getElementById("run-actions");
  if (actions) {
    const showWarning =
      nextStatus.status === "ready" && !nextStatus.has_verification;
    actions.innerHTML = `${renderActionsHtml(nextStatus)}<span id="run-verification-badge" class="badge warn" style="display: ${showWarning ? "inline-flex" : "none"};">NO VERIFICATION</span>`;
    const submitBtn = document.getElementById("submit-run");
    if (submitBtn && nextStatus.can_submit && nextStatus.status !== "auto_failed") {
      submitBtn.addEventListener("click", submitRun);
    }
    const backBtn = document.getElementById("back-cases");
    if (backBtn) {
      backBtn.addEventListener("click", backToCases);
    }
  }

  if (prevStatus && prevStatus.status === "setup_running" && nextStatus.status === "ready") {
    if (!state.setupAlerted) {
      alert("Setup finished");
      state.setupAlerted = true;
    }
  }

  if (prevStatus && prevStatus.status === "verifying" && nextStatus.status === "failed") {
    alert("Submission failed. Fix the issue and submit again.");
  }

  if (prevStatus && prevStatus.last_error !== nextStatus.last_error && nextStatus.last_error) {
    alert(`Error: ${nextStatus.last_error}`);
  }

  updateMetricsView(nextStatus);
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = value;
  }
}

function renderMetricsText(metrics, status) {
  if (!metrics) {
    if (!status || status.status === "idle") {
      return "No metrics available.";
    }
    return "Metrics pending...";
  }
  if (metrics.error) {
    return `Metrics unavailable: ${metrics.error}`;
  }
  if (metrics.status === "pending") {
    return "Metrics pending...";
  }
  if (metrics.status === "missing") {
    return `Metrics file not found: ${metrics.path || "-"}`;
  }
  return JSON.stringify(metrics, null, 2);
}

function updateMetricsView(status) {
  const body = document.getElementById("run-metrics-body");
  if (!body) {
    return;
  }
  body.textContent = renderMetricsText(state.runMetrics, status);
}

function shouldRerender(prevStatus, nextStatus) {
  if (!prevStatus || !state.runRendered) {
    return true;
  }
  const prevCase = prevStatus.case ? prevStatus.case.id : "";
  const nextCase = nextStatus.case ? nextStatus.case.id : "";
  if (prevCase !== nextCase) {
    return true;
  }
  return false;
}

function renderActionsHtml(status) {
  if (status.status === "setup_running") {
    if (status.setup_phase) {
      return `<span class="logs" id="run-current-step">Setup phase: ${escapeHtml(formatSetupPhase(status.setup_phase))} | Step: ${escapeHtml(status.current_step || "waiting...")}</span>`;
    }
    return `<span class="logs" id="run-current-step">Setup running... ${escapeHtml(status.current_step || "")}</span>`;
  }
  if (status.status === "ready" || status.status === "failed") {
    const back = `<button class="button ghost" id="back-cases">Back to cases</button>`;
    return `<button class="button" id="submit-run">Submit</button>${back}`;
  }
  if (status.status === "passed" || status.status === "auto_failed" || status.status === "setup_failed") {
    return `<button class="button ghost" id="back-cases">Back to cases</button>`;
  }
  return "";
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttr(value) {
  return escapeHtml(String(value)).replace(/"/g, "&quot;");
}

function cssEscape(value) {
  const text = String(value || "");
  if (typeof CSS !== "undefined" && CSS.escape) {
    return CSS.escape(text);
  }
  return text.replace(/["\\]/g, "\\$&");
}

function renderSelectOptions(items, selected) {
  return (items || [])
    .map((item) => {
      const sel = item === selected ? " selected" : "";
      return `<option value="${escapeAttr(item)}"${sel}>${escapeHtml(item)}</option>`;
    })
    .join("");
}

function getScopeLabel(scopeType) {
  if (scopeType === "case" && state.currentCaseDetails) {
    return `One Case (run ${state.currentCaseDetails.service}/${state.currentCaseDetails.case})`;
  }
  if (scopeType === "service" && state.currentService) {
    return `One Service (batch --service ${state.currentService})`;
  }
  return "All Services (batch --all)";
}

function renderCommandBuilderPanel(scopeType) {
  const panel = state.commandBuilder;
  if (!panel.visible || panel.scopeType !== scopeType) {
    return "";
  }
  initializeCommandFlags();
  const f = panel.flags;
  const options = state.orchestratorOptions || getFallbackOrchestratorOptions();
  const showAdvanced = panel.showAdvanced;
  const preview = panel.preview || {};
  const errors = preview.errors || [];
  const warnings = preview.warnings || [];

  return `
    <section class="section command-builder" id="command-builder-${scopeType}">
      <div class="section-title">
        <h2>Command Runner</h2>
        <button class="button ghost" type="button" id="cmd-close-${scopeType}">Close</button>
      </div>
      <div class="kicker">${escapeHtml(getScopeLabel(scopeType))}</div>

      <div class="cmd-grid">
        <label class="cmd-field">
          <span>Agent</span>
          <select id="cmd-agent-${scopeType}">${renderSelectOptions(options.choices.agents, f.agent)}</select>
        </label>
        <label class="cmd-field">
          <span>Max Attempts</span>
          <input id="cmd-max-attempts-${scopeType}" value="${escapeAttr(f.max_attempts || "")}" placeholder="(use case/default)" />
        </label>
      </div>

      <div class="cmd-grid">
        <label class="cmd-field checkbox">
          <input type="checkbox" id="cmd-agent-build-${scopeType}" ${f.agent_build ? "checked" : ""} />
          <span>agent-build</span>
        </label>
        <label class="cmd-field">
          <span>Agent Auth Path</span>
          <input id="cmd-agent-auth-path-${scopeType}" value="${escapeAttr(f.agent_auth_path || "")}" placeholder="~/.codex/auth.json" />
        </label>
      </div>

      <label class="cmd-field">
        <span>Agent Command</span>
        <textarea id="cmd-agent-cmd-${scopeType}" rows="3" placeholder="Optional --agent-cmd">${escapeHtml(f.agent_cmd || "")}</textarea>
      </label>

      <div class="status-line">
        <button class="button ghost" type="button" id="cmd-toggle-advanced-${scopeType}">${showAdvanced ? "Hide Advanced" : "Show Advanced"}</button>
        <button class="button" type="button" id="cmd-refresh-${scopeType}">Refresh Command</button>
      </div>

      <div class="cmd-advanced" id="cmd-advanced-${scopeType}" style="display:${showAdvanced ? "block" : "none"};">
        <div class="cmd-grid">
          <label class="cmd-field">
            <span>Sandbox</span>
            <select id="cmd-sandbox-${scopeType}">${renderSelectOptions(options.choices.sandbox, f.sandbox)}</select>
          </label>
          <label class="cmd-field">
            <span>Agent Tag</span>
            <input id="cmd-agent-tag-${scopeType}" value="${escapeAttr(f.agent_tag || "")}" />
          </label>
          <label class="cmd-field checkbox">
            <input type="checkbox" id="cmd-agent-cleanup-${scopeType}" ${f.agent_cleanup ? "checked" : ""} />
            <span>agent-cleanup</span>
          </label>
          <label class="cmd-field checkbox">
            <input type="checkbox" id="cmd-manual-start-${scopeType}" ${f.manual_start ? "checked" : ""} />
            <span>manual-start</span>
          </label>
        </div>

        <div class="cmd-grid">
          <label class="cmd-field">
            <span>LLM Env File</span>
            <input id="cmd-llm-env-file-${scopeType}" value="${escapeAttr(f.llm_env_file || "")}" />
          </label>
          <label class="cmd-field">
            <span>Agent Auth Dest</span>
            <input id="cmd-agent-auth-dest-${scopeType}" value="${escapeAttr(f.agent_auth_dest || "")}" />
          </label>
          <label class="cmd-field">
            <span>Docker Image</span>
            <input id="cmd-docker-image-${scopeType}" value="${escapeAttr(f.docker_image || "")}" />
          </label>
          <label class="cmd-field">
            <span>Source Kubeconfig</span>
            <input id="cmd-source-kubeconfig-${scopeType}" value="${escapeAttr(f.source_kubeconfig || "")}" />
          </label>
        </div>

        <div class="cmd-grid">
          <label class="cmd-field">
            <span>Proxy Server</span>
            <input id="cmd-proxy-server-${scopeType}" value="${escapeAttr(f.proxy_server || "")}" />
          </label>
          <label class="cmd-field">
            <span>Real Kubectl</span>
            <input id="cmd-real-kubectl-${scopeType}" value="${escapeAttr(f.real_kubectl || "")}" />
          </label>
        </div>

        <div class="cmd-grid">
          <label class="cmd-field">
            <span>Submit Timeout</span>
            <input id="cmd-submit-timeout-${scopeType}" value="${escapeAttr(f.submit_timeout || "")}" />
          </label>
          <label class="cmd-field">
            <span>Setup Timeout</span>
            <input id="cmd-setup-timeout-${scopeType}" value="${escapeAttr(f.setup_timeout || "")}" />
          </label>
          <label class="cmd-field">
            <span>Setup Timeout Mode</span>
            <select id="cmd-setup-timeout-mode-${scopeType}">${renderSelectOptions(options.choices.setup_timeout_mode, f.setup_timeout_mode)}</select>
          </label>
          <label class="cmd-field">
            <span>Verify Timeout</span>
            <input id="cmd-verify-timeout-${scopeType}" value="${escapeAttr(f.verify_timeout || "")}" />
          </label>
          <label class="cmd-field">
            <span>Cleanup Timeout</span>
            <input id="cmd-cleanup-timeout-${scopeType}" value="${escapeAttr(f.cleanup_timeout || "")}" />
          </label>
        </div>

        ${scopeType !== "case" ? `
        <div class="cmd-grid">
          <label class="cmd-field">
            <span>Batch Results JSON</span>
            <input id="cmd-results-json-${scopeType}" value="${escapeAttr(f.results_json || "")}" />
          </label>
        </div>
        ` : ""}
      </div>

      ${errors.length ? `<div class="cmd-errors">${errors.map((e) => `<div>${escapeHtml(e)}</div>`).join("")}</div>` : ""}
      ${warnings.length ? `<div class="cmd-warnings">${warnings.map((w) => `<div>${escapeHtml(w)}</div>`).join("")}</div>` : ""}

      <div class="kicker">One-line</div>
      <div class="pre" id="cmd-one-line-${scopeType}">${escapeHtml(preview.command_one_line || "(click Refresh Command)")}</div>
      <div class="status-line">
        <button class="button ghost" type="button" id="cmd-copy-one-${scopeType}">Copy One-line</button>
      </div>

      <div class="kicker">Multi-line</div>
      <div class="pre" id="cmd-multi-line-${scopeType}">${escapeHtml(preview.command_multi_line || "")}</div>
      <div class="status-line">
        <button class="button ghost" type="button" id="cmd-copy-multi-${scopeType}">Copy Multi-line</button>
      </div>
    </section>
  `;
}

function getScopePayload(scopeType) {
  if (scopeType === "case") {
    return {
      type: "case",
      service: state.currentCaseDetails?.service || state.currentService || "",
      case: state.currentCaseDetails?.case || "",
    };
  }
  if (scopeType === "service") {
    return {
      type: "service",
      service: state.currentService || "",
    };
  }
  return { type: "all" };
}

function openCommandBuilder(scopeType) {
  initializeCommandFlags();
  state.commandBuilder.visible = true;
  state.commandBuilder.scopeType = scopeType;
  state.commandBuilder.preview = null;
  rerenderCurrentView();
  generateCliPreview(scopeType);
}

function closeCommandBuilder() {
  state.commandBuilder.visible = false;
  state.commandBuilder.preview = null;
  rerenderCurrentView();
}

function rerenderCurrentView() {
  updateTopNav();
  if (state.viewMode === "judge") {
    renderJudgePage();
    return;
  }
  if (state.viewMode === "workflow") {
    renderWorkflowPage();
    return;
  }
  if (state.runStatus && state.runStatus.status && state.runStatus.status !== "idle") {
    renderRun();
    return;
  }
  if (state.currentCaseDetails) {
    renderCaseDetail();
    return;
  }
  if (state.currentService) {
    renderCases();
    return;
  }
  renderHome();
}

function collectCommandFlags(scopeType) {
  const pick = (id) => document.getElementById(`cmd-${id}-${scopeType}`);
  const getVal = (id) => {
    const el = pick(id);
    return el ? el.value : "";
  };
  const getChecked = (id) => {
    const el = pick(id);
    return !!(el && el.checked);
  };

  state.commandBuilder.flags = {
    agent: getVal("agent"),
    agent_build: getChecked("agent-build"),
    max_attempts: getVal("max-attempts"),
    agent_auth_path: getVal("agent-auth-path"),
    agent_cmd: getVal("agent-cmd"),
    sandbox: getVal("sandbox"),
    agent_tag: getVal("agent-tag"),
    agent_cleanup: getChecked("agent-cleanup"),
    manual_start: getChecked("manual-start"),
    llm_env_file: getVal("llm-env-file"),
    agent_auth_dest: getVal("agent-auth-dest"),
    docker_image: getVal("docker-image"),
    source_kubeconfig: getVal("source-kubeconfig"),
    proxy_server: getVal("proxy-server"),
    real_kubectl: getVal("real-kubectl"),
    submit_timeout: getVal("submit-timeout"),
    setup_timeout: getVal("setup-timeout"),
    setup_timeout_mode: getVal("setup-timeout-mode"),
    verify_timeout: getVal("verify-timeout"),
    cleanup_timeout: getVal("cleanup-timeout"),
    results_json: getVal("results-json"),
  };
}

async function generateCliPreview(scopeType) {
  collectCommandFlags(scopeType);
  const payload = {
    scope: getScopePayload(scopeType),
    flags: state.commandBuilder.flags,
  };

  let preview;
  try {
    const resp = await fetch("/api/orchestrator/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    preview = await resp.json();
  } catch (err) {
    preview = {
      ok: false,
      errors: [err.message || String(err)],
      warnings: [],
      command_one_line: "",
      command_multi_line: "",
    };
  }

  state.commandBuilder.preview = preview;
  rerenderCurrentView();
}

function attachCommandBuilderHandlers(scopeType) {
  if (!state.commandBuilder.visible || state.commandBuilder.scopeType !== scopeType) {
    return;
  }

  const closeBtn = document.getElementById(`cmd-close-${scopeType}`);
  if (closeBtn) {
    closeBtn.addEventListener("click", closeCommandBuilder);
  }

  const refreshBtn = document.getElementById(`cmd-refresh-${scopeType}`);
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => generateCliPreview(scopeType));
  }

  const toggleBtn = document.getElementById(`cmd-toggle-advanced-${scopeType}`);
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      state.commandBuilder.showAdvanced = !state.commandBuilder.showAdvanced;
      rerenderCurrentView();
    });
  }

  const copyOne = document.getElementById(`cmd-copy-one-${scopeType}`);
  if (copyOne) {
    copyOne.addEventListener("click", async () => {
      const text = state.commandBuilder.preview?.command_one_line || "";
      if (!text) {
        return;
      }
      await copyText(text);
      alert("Copied one-line command");
    });
  }

  const copyMulti = document.getElementById(`cmd-copy-multi-${scopeType}`);
  if (copyMulti) {
    copyMulti.addEventListener("click", async () => {
      const text = state.commandBuilder.preview?.command_multi_line || "";
      if (!text) {
        return;
      }
      await copyText(text);
      alert("Copied multi-line command");
    });
  }
}

async function copyText(value) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function _workflowDefaultStage() {
  const services = Object.keys(state.workflow.catalog || {});
  const service = services[0] || "";
  const cases = service ? _workflowCaseNames(service) : [];
  const caseName = cases[0] || "";
  const idx = (state.workflow.builder.draft.spec.stages || []).length + 1;
  return {
    id: `stage_${idx}`,
    service,
    case: caseName,
    namespaces: [],
    namespace_bindings: {},
    max_attempts: null,
    param_overrides: {},
  };
}

function _workflowCaseRows(service) {
  const rows = (state.workflow.catalog || {})[service];
  return Array.isArray(rows) ? rows : [];
}

function _workflowCaseNames(service) {
  return _workflowCaseRows(service)
    .map((row) => String(row?.case || "").trim())
    .filter(Boolean);
}

function _workflowFindCaseRow(service, caseName) {
  const target = String(caseName || "").trim();
  return _workflowCaseRows(service).find((row) => String(row?.case || "").trim() === target) || null;
}

function _workflowCaseParamsKey(service, caseName) {
  const svc = String(service || "").trim();
  const cas = String(caseName || "").trim();
  if (!svc || !cas) {
    return "";
  }
  return `${svc}/${cas}`;
}

function _workflowNormalizeParamDefinitions(rawDefinitions) {
  if (!rawDefinitions || typeof rawDefinitions !== "object" || Array.isArray(rawDefinitions)) {
    return {};
  }
  const out = {};
  Object.entries(rawDefinitions).forEach(([rawName, rawSpec]) => {
    const name = String(rawName || "").trim();
    if (!name) {
      return;
    }
    const spec = rawSpec && typeof rawSpec === "object" && !Array.isArray(rawSpec)
      ? rawSpec
      : { type: "string", default: rawSpec };
    const normalized = {
      type: String(spec.type || "string").trim().toLowerCase() || "string",
    };
    if (Object.prototype.hasOwnProperty.call(spec, "default")) {
      normalized.default = spec.default;
    }
    if (spec.required) {
      normalized.required = true;
    }
    if (Array.isArray(spec.values)) {
      normalized.values = [...spec.values];
    }
    if (Object.prototype.hasOwnProperty.call(spec, "min")) {
      normalized.min = spec.min;
    }
    if (Object.prototype.hasOwnProperty.call(spec, "max")) {
      normalized.max = spec.max;
    }
    if (Object.prototype.hasOwnProperty.call(spec, "pattern") && spec.pattern !== null) {
      normalized.pattern = String(spec.pattern);
    }
    if (Object.prototype.hasOwnProperty.call(spec, "description") && spec.description !== null) {
      normalized.description = String(spec.description);
    }
    out[name] = normalized;
  });
  return out;
}

async function _workflowEnsureCaseParams(service, caseName) {
  const key = _workflowCaseParamsKey(service, caseName);
  if (!key) {
    return { status: "ready", definitions: {} };
  }
  state.workflow.caseParams = state.workflow.caseParams || {};
  const existing = state.workflow.caseParams[key];
  if (existing && existing.status === "ready") {
    return existing;
  }
  if (existing && existing.status === "loading" && existing.promise) {
    await existing.promise;
    return state.workflow.caseParams[key] || existing;
  }
  const row = _workflowFindCaseRow(service, caseName);
  if (!row || !row.id) {
    const missing = { status: "error", error: `case id not found for ${key}`, definitions: {} };
    state.workflow.caseParams[key] = missing;
    return missing;
  }
  const promise = (async () => {
    try {
      const details = await fetchJSON(`/api/cases/${encodeURIComponent(row.id)}`);
      if (details && details.error) {
        state.workflow.caseParams[key] = { status: "error", error: String(details.error), definitions: {} };
        return;
      }
      const defs = _workflowNormalizeParamDefinitions((details?.params || {}).definitions || {});
      state.workflow.caseParams[key] = { status: "ready", definitions: defs, error: null };
    } catch (err) {
      state.workflow.caseParams[key] = { status: "error", error: err.message || String(err), definitions: {} };
    }
  })();
  state.workflow.caseParams[key] = { status: "loading", definitions: {}, error: null, promise };
  await promise;
  return state.workflow.caseParams[key];
}

function _workflowCaseParamDefinitions(service, caseName) {
  const key = _workflowCaseParamsKey(service, caseName);
  if (!key) {
    return null;
  }
  const row = (state.workflow.caseParams || {})[key];
  if (!row || row.status !== "ready") {
    return null;
  }
  return row.definitions || {};
}

function _workflowCaseParamsStatus(service, caseName) {
  const key = _workflowCaseParamsKey(service, caseName);
  if (!key) {
    return null;
  }
  return (state.workflow.caseParams || {})[key] || null;
}

function _workflowPruneStageParamOverrides(stage) {
  if (!stage || typeof stage !== "object") {
    return stage;
  }
  const defs = _workflowCaseParamDefinitions(stage.service, stage.case);
  if (!defs) {
    return stage;
  }
  const overrides = stage.param_overrides && typeof stage.param_overrides === "object"
    ? stage.param_overrides
    : {};
  const cleaned = {};
  Object.keys(overrides).forEach((name) => {
    if (Object.prototype.hasOwnProperty.call(defs, name)) {
      cleaned[name] = overrides[name];
    }
  });
  stage.param_overrides = cleaned;
  return stage;
}

async function _workflowPrimeDraftCaseParams(draft) {
  const stages = Array.isArray(draft?.spec?.stages) ? draft.spec.stages : [];
  await Promise.all(
    stages.map(async (stage) => {
      const service = String(stage?.service || "").trim();
      const caseName = String(stage?.case || "").trim();
      if (!service || !caseName) {
        return;
      }
      await _workflowEnsureCaseParams(service, caseName);
      _workflowPruneStageParamOverrides(stage);
    })
  );
}

function _workflowParseParamOverrideValue(rawText, spec) {
  const type = String(spec?.type || "string").trim().toLowerCase();
  if (type === "bool") {
    if (rawText === "true") {
      return true;
    }
    if (rawText === "false") {
      return false;
    }
    return rawText;
  }
  if (type === "int") {
    if (/^-?\d+$/.test(rawText)) {
      return Number(rawText);
    }
    return rawText;
  }
  if (type === "float" || type === "number") {
    const parsed = Number(rawText);
    return Number.isFinite(parsed) ? parsed : rawText;
  }
  return rawText;
}

const _WORKFLOW_STAGE_PARAM_REF_RE = /^\s*\$\{stages\.([a-zA-Z0-9_.-]+)\.params\.([a-zA-Z0-9_.-]+)\}\s*$/;

function _workflowParseStageParamRefExpr(value) {
  if (typeof value !== "string") {
    return null;
  }
  const match = value.match(_WORKFLOW_STAGE_PARAM_REF_RE);
  if (!match) {
    return null;
  }
  return { stageId: match[1], paramName: match[2] };
}

function _workflowBuildStageParamRefExpr(stageId, paramName) {
  const sid = String(stageId || "").trim();
  const pname = String(paramName || "").trim();
  if (!sid || !pname) {
    return "";
  }
  return `\${stages.${sid}.params.${pname}}`;
}

function _workflowParamRefUiKey(stageId, paramName) {
  return `${String(stageId || "").trim()}::${String(paramName || "").trim()}`;
}

function _workflowEnsureParamRefExpandedState() {
  const builder = state.workflow?.builder;
  if (!builder || typeof builder !== "object") {
    return {};
  }
  if (!builder.param_ref_expanded || typeof builder.param_ref_expanded !== "object" || Array.isArray(builder.param_ref_expanded)) {
    builder.param_ref_expanded = {};
  }
  return builder.param_ref_expanded;
}

function _workflowIsParamRefExpanded(stageId, paramName, value) {
  const key = _workflowParamRefUiKey(stageId, paramName);
  const expandedState = _workflowEnsureParamRefExpandedState();
  if (Object.prototype.hasOwnProperty.call(expandedState, key)) {
    return !!expandedState[key];
  }
  return !!_workflowParseStageParamRefExpr(String(value || ""));
}

function _workflowSetParamRefExpanded(stageId, paramName, expanded) {
  const key = _workflowParamRefUiKey(stageId, paramName);
  const expandedState = _workflowEnsureParamRefExpandedState();
  expandedState[key] = !!expanded;
}

function _workflowDropStageParamRefUiState(stageId) {
  const sid = String(stageId || "").trim();
  if (!sid) {
    return;
  }
  const prefix = `${sid}::`;
  const expandedState = _workflowEnsureParamRefExpandedState();
  Object.keys(expandedState).forEach((key) => {
    if (key.startsWith(prefix)) {
      delete expandedState[key];
    }
  });
}

function _workflowStageParamNamesForReference(stage) {
  const defs = _workflowCaseParamDefinitions(stage?.service, stage?.case) || {};
  const names = new Set(Object.keys(defs || {}));
  const overrides = stage?.param_overrides && typeof stage.param_overrides === "object"
    ? stage.param_overrides
    : {};
  Object.keys(overrides).forEach((name) => {
    const clean = String(name || "").trim();
    if (clean) {
      names.add(clean);
    }
  });
  return Array.from(names).sort((a, b) => a.localeCompare(b));
}

function _workflowRefStageChoicesForIndex(stageIndex) {
  const stages = Array.isArray(state.workflow?.builder?.draft?.spec?.stages)
    ? state.workflow.builder.draft.spec.stages
    : [];
  const out = [];
  for (let i = 0; i < Math.max(0, Number(stageIndex || 0)); i += 1) {
    const stage = stages[i] || {};
    const stageId = String(stage.id || "").trim();
    if (!stageId) {
      continue;
    }
    const params = _workflowStageParamNamesForReference(stage);
    if (!params.length) {
      continue;
    }
    out.push({
      stageId,
      label: `${stageId} (${String(stage.service || "?")}/${String(stage.case || "?")})`,
      params,
    });
  }
  return out;
}

function _workflowRefParamOptionsHtml(stageId, stageIndex, selectedParam) {
  const selectedStageId = String(stageId || "").trim();
  if (!selectedStageId) {
    return '<option value="" selected>(select param)</option>';
  }
  const choices = _workflowRefStageChoicesForIndex(stageIndex);
  const row = choices.find((it) => String(it.stageId || "") === selectedStageId);
  const params = row ? row.params : [];
  const current = String(selectedParam || "");
  const options = ['<option value="">(select param)</option>']
    .concat(
      params.map((name) => (
        `<option value="${escapeAttr(name)}" ${name === current ? "selected" : ""}>${escapeHtml(name)}</option>`
      ))
    );
  return options.join("");
}

function _workflowUpdateParamRefSelectOptions(card, stageId, paramName) {
  if (!(card instanceof Element)) {
    return;
  }
  const currentStageIndex = Number(card.getAttribute("data-workflow-stage-index") || "0");
  const stageSelect = card.querySelector(
    `[data-workflow-param-ref-stage="${cssEscape(paramName)}"][data-workflow-stage-id="${cssEscape(stageId)}"]`
  );
  const paramSelect = card.querySelector(
    `[data-workflow-param-ref-param="${cssEscape(paramName)}"][data-workflow-stage-id="${cssEscape(stageId)}"]`
  );
  if (!stageSelect || !paramSelect) {
    return;
  }
  const selectedStageId = String(stageSelect.value || "").trim();
  const prevSelectedParam = String(paramSelect.value || "").trim();
  paramSelect.innerHTML = _workflowRefParamOptionsHtml(selectedStageId, currentStageIndex, prevSelectedParam);
  if (!selectedStageId) {
    paramSelect.disabled = true;
    return;
  }
  paramSelect.disabled = false;
  const hasParam = Array.from(paramSelect.options || []).some((it) => String(it.value || "").trim());
  if (hasParam && !String(paramSelect.value || "").trim()) {
    const firstValue = Array.from(paramSelect.options || []).map((it) => String(it.value || "").trim()).find(Boolean) || "";
    if (firstValue) {
      paramSelect.value = firstValue;
    }
  }
}

function _workflowApplyParamRefSelection(card, stageId, paramName) {
  if (!(card instanceof Element)) {
    return;
  }
  const stageSelect = card.querySelector(
    `[data-workflow-param-ref-stage="${cssEscape(paramName)}"][data-workflow-stage-id="${cssEscape(stageId)}"]`
  );
  const paramSelect = card.querySelector(
    `[data-workflow-param-ref-param="${cssEscape(paramName)}"][data-workflow-stage-id="${cssEscape(stageId)}"]`
  );
  const inputEl = card.querySelector(
    `[data-workflow-field="param:${cssEscape(paramName)}"][data-workflow-stage-id="${cssEscape(stageId)}"]`
  );
  if (!stageSelect || !paramSelect || !inputEl) {
    return;
  }
  const refStageId = String(stageSelect.value || "").trim();
  const refParamName = String(paramSelect.value || "").trim();
  if (!refStageId || !refParamName) {
    return;
  }
  inputEl.value = _workflowBuildStageParamRefExpr(refStageId, refParamName);
}

function _workflowYamlScalar(value) {
  if (value === null) {
    return "null";
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : JSON.stringify(String(value));
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  return JSON.stringify(String(value));
}

function _workflowValidateDraft(draft) {
  const errors = [];
  const warnings = [];
  if (!draft || typeof draft !== "object") {
    return { ok: false, errors: ["workflow draft is invalid"], warnings };
  }
  const name = String(((draft.metadata || {}).name || "")).trim();
  if (!name) {
    errors.push("metadata.name is required");
  }
  const spec = draft.spec || {};
  const mode = String(spec.prompt_mode || "progressive").trim();
  if (!["progressive", "concat_stateful", "concat_blind"].includes(mode)) {
    errors.push("spec.prompt_mode must be progressive|concat_stateful|concat_blind");
  }
  const finalSweepMode = String(spec.final_sweep_mode || "full").trim().toLowerCase();
  if (!["full", "off"].includes(finalSweepMode)) {
    errors.push("spec.final_sweep_mode must be full|off");
  }
  const stages = Array.isArray(spec.stages) ? spec.stages : [];
  if (!stages.length) {
    errors.push("spec.stages must include at least one stage");
  }
  const seen = new Set();
  for (let i = 0; i < stages.length; i += 1) {
    const stage = stages[i] || {};
    const id = String(stage.id || "").trim();
    const service = String(stage.service || "").trim();
    const caseName = String(stage.case || "").trim();
    if (!id) {
      errors.push(`stage[${i + 1}] id is required`);
    }
    if (id && seen.has(id)) {
      errors.push(`stage id duplicated: ${id}`);
    }
    seen.add(id);
    if (!service) {
      errors.push(`stage[${i + 1}] service is required`);
    }
    if (!caseName) {
      errors.push(`stage[${i + 1}] case is required`);
    }
    const known = _workflowCaseNames(service);
    if (service && caseName && known.length && !known.includes(caseName)) {
      warnings.push(`stage[${i + 1}] case not found under service catalog: ${service}/${caseName}`);
    }
    const defs = _workflowCaseParamDefinitions(service, caseName);
    if (defs) {
      const overrides = stage.param_overrides && typeof stage.param_overrides === "object" ? stage.param_overrides : {};
      Object.keys(overrides).forEach((name) => {
        if (!Object.prototype.hasOwnProperty.call(defs, name)) {
          errors.push(`stage[${i + 1}] unknown param override: ${name}`);
        }
      });
      Object.entries(defs).forEach(([name, spec]) => {
        const required = !!spec.required;
        const hasDefault = Object.prototype.hasOwnProperty.call(spec, "default");
        const hasOverride = Object.prototype.hasOwnProperty.call(overrides, name);
        if (required && !hasDefault && !hasOverride) {
          errors.push(`stage[${i + 1}] missing required param override: ${name}`);
        }
      });
    } else {
      const paramState = _workflowCaseParamsStatus(service, caseName);
      if (paramState && paramState.status === "error" && paramState.error) {
        warnings.push(`stage[${i + 1}] params unavailable: ${paramState.error}`);
      }
    }
  }
  return { ok: errors.length === 0, errors, warnings };
}

function _workflowToYaml(draft) {
  const out = [];
  const name = String(((draft.metadata || {}).name || "")).trim() || "workflow";
  const spec = draft.spec || {};
  out.push("apiVersion: benchmark/v1alpha1");
  out.push("kind: Workflow");
  out.push("metadata:");
  out.push(`  name: ${name}`);
  out.push("spec:");
  out.push(`  prompt_mode: ${String(spec.prompt_mode || "progressive")}`);
  out.push(`  final_sweep_mode: ${String(spec.final_sweep_mode || "full").trim().toLowerCase() || "full"}`);
  const namespaces = Array.isArray(spec.namespaces) ? spec.namespaces.filter((it) => String(it || "").trim()) : [];
  if (namespaces.length) {
    out.push("  namespaces:");
    namespaces.forEach((ns) => out.push(`  - ${String(ns).trim()}`));
  }
  out.push("  stages:");
  const stages = Array.isArray(spec.stages) ? spec.stages : [];
  stages.forEach((stage) => {
    out.push(`  - id: ${String(stage.id || "").trim()}`);
    out.push(`    service: ${String(stage.service || "").trim()}`);
    out.push(`    case: ${String(stage.case || "").trim()}`);
    const stageNs = Array.isArray(stage.namespaces) ? stage.namespaces.filter((it) => String(it || "").trim()) : [];
    if (stageNs.length) {
      out.push("    namespaces:");
      stageNs.forEach((ns) => out.push(`    - ${String(ns).trim()}`));
    }
    const bindings = stage.namespace_bindings && typeof stage.namespace_bindings === "object" ? stage.namespace_bindings : {};
    const bindingKeys = Object.keys(bindings).filter((k) => String(k || "").trim() && String(bindings[k] || "").trim());
    if (bindingKeys.length) {
      out.push("    namespace_binding:");
      bindingKeys.forEach((k) => out.push(`      ${k}: ${String(bindings[k]).trim()}`));
    }
    if (stage.max_attempts !== null && stage.max_attempts !== undefined && String(stage.max_attempts).trim() !== "") {
      out.push(`    max_attempts: ${String(stage.max_attempts).trim()}`);
    }
    const overrides = stage.param_overrides && typeof stage.param_overrides === "object" ? stage.param_overrides : {};
    const overrideKeys = Object.keys(overrides).filter((k) => String(k || "").trim());
    if (overrideKeys.length) {
      out.push("    param_overrides:");
      overrideKeys.forEach((name) => out.push(`      ${name}: ${_workflowYamlScalar(overrides[name])}`));
    }
  });
  return out.join("\n") + "\n";
}

function _workflowRefreshDerived() {
  const draft = state.workflow.builder.draft;
  state.workflow.builder.validation = _workflowValidateDraft(draft);
  state.workflow.builder.yaml_preview = _workflowToYaml(draft);
  state.workflow.builder.last_updated_ts = new Date().toISOString();
}

function _workflowRenderServiceOptions(selected) {
  const services = Object.keys(state.workflow.catalog || {});
  const rows = services.map((name) => `<option value="${escapeAttr(name)}" ${name === selected ? "selected" : ""}>${escapeHtml(name)}</option>`);
  return rows.join("");
}

function _workflowRenderCaseOptions(service, selected) {
  const rows = _workflowCaseNames(service).map((name) => `<option value="${escapeAttr(name)}" ${name === selected ? "selected" : ""}>${escapeHtml(name)}</option>`);
  return rows.join("");
}

function _workflowRenderStageParamField(stageId, stageIndex, paramName, spec, value) {
  const field = `param:${paramName}`;
  const paramType = String(spec?.type || "string").trim().toLowerCase();
  const hasOverride = value !== null && value !== undefined && String(value) !== "";
  const valueText = hasOverride ? String(value) : "";
  const refParsed = _workflowParseStageParamRefExpr(valueText);
  const refChoices = _workflowRefStageChoicesForIndex(stageIndex);
  const refStageId = refParsed ? String(refParsed.stageId || "") : "";
  const refParamName = refParsed ? String(refParsed.paramName || "") : "";
  const defaultLabel = Object.prototype.hasOwnProperty.call(spec || {}, "default")
    ? ` default=${JSON.stringify(spec.default)}`
    : "";
  const requiredLabel = spec?.required ? " required" : "";
  const fieldLabel = `${paramName} (${paramType}${requiredLabel})${defaultLabel}`;
  const refPickerSupported = paramType !== "bool" && !(paramType === "enum" && Array.isArray(spec?.values) && spec.values.length);
  const refExpanded = refPickerSupported && _workflowIsParamRefExpanded(stageId, paramName, valueText);
  const refToggleHtml = refPickerSupported
    ? `<button class="button ghost workflow-param-ref-toggle" type="button" data-workflow-action="toggle-param-ref" data-workflow-stage-id="${escapeAttr(stageId)}" data-workflow-param-name="${escapeAttr(paramName)}">${refExpanded ? "Hide Ref" : "Add Ref"}</button>`
    : "";
  const refStageOptions = ['<option value="">(literal/manual)</option>']
    .concat(
      refChoices.map((choice) => (
        `<option value="${escapeAttr(choice.stageId)}" ${choice.stageId === refStageId ? "selected" : ""}>${escapeHtml(choice.label)}</option>`
      ))
    )
    .join("");
  const refParamOptions = _workflowRefParamOptionsHtml(refStageId, stageIndex, refParamName);
  const refPickerHtml = refPickerSupported && refExpanded
    ? `
      <div class="workflow-param-ref-panel">
        <label class="cmd-field">
          <span>Ref stage</span>
          <select data-workflow-param-ref-stage="${escapeAttr(paramName)}" data-workflow-stage-id="${escapeAttr(stageId)}">
            ${refStageOptions}
          </select>
        </label>
        <label class="cmd-field">
          <span>Ref param</span>
          <select data-workflow-param-ref-param="${escapeAttr(paramName)}" data-workflow-stage-id="${escapeAttr(stageId)}" ${refStageId ? "" : "disabled"}>
            ${refParamOptions}
          </select>
        </label>
      </div>
      ${refChoices.length ? "" : '<div class="logs workflow-param-ref-empty">No prior stage params available for reference.</div>'}
    `
    : "";
  let inputHtml = "";
  if (paramType === "bool") {
    const current = hasOverride ? String(value) : "";
    inputHtml = `
      <select data-workflow-field="${escapeAttr(field)}" data-workflow-stage-id="${escapeAttr(stageId)}">
        <option value="" ${current === "" ? "selected" : ""}>(use default)</option>
        <option value="true" ${current === "true" ? "selected" : ""}>true</option>
        <option value="false" ${current === "false" ? "selected" : ""}>false</option>
      </select>
    `;
  } else if (paramType === "enum" && Array.isArray(spec?.values) && spec.values.length) {
    const current = hasOverride ? String(value) : "";
    const options = spec.values
      .map((item) => String(item))
      .map((item) => `<option value="${escapeAttr(item)}" ${item === current ? "selected" : ""}>${escapeHtml(item)}</option>`)
      .join("");
    inputHtml = `
      <select data-workflow-field="${escapeAttr(field)}" data-workflow-stage-id="${escapeAttr(stageId)}">
        <option value="" ${current === "" ? "selected" : ""}>(use default)</option>
        ${options}
      </select>
    `;
  } else {
    const numericType = paramType === "int" || paramType === "float" || paramType === "number";
    const inputType = "text";
    const inputModeAttr = numericType ? ' inputmode="numeric"' : "";
    const patternAttr = (spec?.pattern && !refParsed) ? ` pattern="${escapeAttr(spec.pattern)}"` : "";
    const placeholder = Object.prototype.hasOwnProperty.call(spec || {}, "default")
      ? ` placeholder="default: ${escapeAttr(spec.default)}"`
      : "";
    inputHtml = `<input type="${inputType}" data-workflow-field="${escapeAttr(field)}" data-workflow-stage-id="${escapeAttr(stageId)}" value="${hasOverride ? escapeAttr(value) : ""}"${inputModeAttr}${patternAttr}${placeholder} />`;
  }
  return `
    <div class="cmd-field workflow-param-field">
      <div class="status-line workflow-param-field-head">
        <span>${escapeHtml(fieldLabel)}</span>
        ${refToggleHtml}
      </div>
      ${inputHtml}
      ${refPickerHtml}
    </div>
  `;
}

function _workflowRenderStageParams(stage, stageId, stageIndex, service, caseName) {
  const status = _workflowCaseParamsStatus(service, caseName);
  if (status && status.status === "loading") {
    return '<div class="logs" style="grid-column:1/-1;">Loading params...</div>';
  }
  if (status && status.status === "error") {
    return `<div class="cmd-warnings" style="grid-column:1/-1;">Params unavailable: ${escapeHtml(status.error || "unknown error")}</div>`;
  }
  const defs = _workflowCaseParamDefinitions(service, caseName) || {};
  const names = Object.keys(defs);
  if (!names.length) {
    return '<div class="logs" style="grid-column:1/-1;">No params for this case.</div>';
  }
  const overrides = stage.param_overrides && typeof stage.param_overrides === "object" ? stage.param_overrides : {};
  const rows = names
    .sort((a, b) => a.localeCompare(b))
    .map((name) => _workflowRenderStageParamField(stageId, stageIndex, name, defs[name], overrides[name]));
  return rows.join("");
}

function _workflowStageSummaryText(stage, service, caseName, namespaces, overrideCount) {
  const summary = [];
  summary.push(String(stage.id || "(missing id)"));
  if (service || caseName) {
    summary.push(`${service || "?"}/${caseName || "?"}`);
  }
  if (namespaces) {
    summary.push(`ns: ${namespaces}`);
  }
  summary.push(`params: ${overrideCount}`);
  return summary.join(" · ");
}

function renderWorkflowBuilderStageCard(stage, index) {
  const id = String(stage.id || "");
  const service = String(stage.service || "");
  const caseName = String(stage.case || "");
  const namespaces = Array.isArray(stage.namespaces) ? stage.namespaces.join(", ") : "";
  const bindings = stage.namespace_bindings && typeof stage.namespace_bindings === "object" ? Object.entries(stage.namespace_bindings).map(([k, v]) => `${k}:${v}`).join(", ") : "";
  const overrideCount = stage.param_overrides && typeof stage.param_overrides === "object"
    ? Object.keys(stage.param_overrides).filter((name) => String(name || "").trim()).length
    : 0;
  const expandedId = String(state.workflow.builder.expanded_stage_id || "");
  const expanded = expandedId === id;
  const summaryText = _workflowStageSummaryText(stage, service, caseName, namespaces, overrideCount);
  return `
    <div class="workflow-stage-card" draggable="true" data-workflow-stage-id="${escapeAttr(id)}" data-workflow-stage-index="${index}">
      <div class="status-line workflow-stage-topline" style="justify-content:space-between;" data-workflow-stage-toggle="1">
        <div class="workflow-stage-topline-main">
          <strong>Stage ${index + 1}</strong>
          <div class="logs workflow-stage-topline-summary">
            ${escapeHtml(summaryText)}
          </div>
        </div>
        <div class="status-line workflow-stage-topline-actions">
          <span class="badge">Params: ${overrideCount}</span>
          <button class="button ghost" type="button" data-workflow-action="remove-stage" data-workflow-stage-id="${escapeAttr(id)}">Remove</button>
        </div>
      </div>
      <div class="workflow-stage-grid" style="margin-top:8px; display:${expanded ? "grid" : "none"};">
        <label class="cmd-field">
          <span>id</span>
          <input data-workflow-field="id" data-workflow-stage-id="${escapeAttr(id)}" value="${escapeAttr(id)}" />
        </label>
        <label class="cmd-field">
          <span>service</span>
          <select data-workflow-field="service" data-workflow-stage-id="${escapeAttr(id)}">${_workflowRenderServiceOptions(service)}</select>
        </label>
        <label class="cmd-field">
          <span>case</span>
          <select data-workflow-field="case" data-workflow-stage-id="${escapeAttr(id)}">${_workflowRenderCaseOptions(service, caseName)}</select>
        </label>
        <label class="cmd-field">
          <span>max_attempts</span>
          <input data-workflow-field="max_attempts" data-workflow-stage-id="${escapeAttr(id)}" value="${stage.max_attempts || ""}" placeholder="(optional)" />
        </label>
        <label class="cmd-field">
          <span>namespaces (csv)</span>
          <input data-workflow-field="namespaces" data-workflow-stage-id="${escapeAttr(id)}" value="${escapeAttr(namespaces)}" placeholder="cluster_a,cluster_b" />
        </label>
        <label class="cmd-field">
          <span>namespace_binding (csv role:alias)</span>
          <input data-workflow-field="bindings" data-workflow-stage-id="${escapeAttr(id)}" value="${escapeAttr(bindings)}" placeholder="source:cluster_a,target:cluster_b" />
        </label>
        <div class="workflow-stage-params" style="grid-column:1/-1;">
          <div class="kicker">param_overrides</div>
          <div class="workflow-stage-grid">
            ${_workflowRenderStageParams(stage, id, index, service, caseName)}
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderWorkflowBuilderPanel() {
  const builder = state.workflow.builder;
  const draft = builder.draft || { metadata: {}, spec: { stages: [] } };
  const validation = builder.validation || { ok: false, errors: [], warnings: [] };
  const stages = (draft.spec && draft.spec.stages) || [];
  const promptMode = String((draft.spec && draft.spec.prompt_mode) || "progressive");
  const finalSweepMode = String((draft.spec && draft.spec.final_sweep_mode) || "full").toLowerCase();
  const namespaces = Array.isArray(draft.spec?.namespaces) ? draft.spec.namespaces.join(", ") : "";
  const preview = builder.cli_preview || {};
  return `
    <section class="section workflow-columns">
      <div class="workflow-panel">
        <div class="section-title"><h2>Workflow Builder</h2></div>
        <div class="kicker">Builder does not write workflow.yaml. Copy preview and save manually.</div>
        <div class="cmd-grid">
          <label class="cmd-field">
            <span>workflow name</span>
            <input id="wf-name" value="${escapeAttr(draft.metadata?.name || "")}" />
          </label>
          <label class="cmd-field">
            <span>workflow file path (for CLI)</span>
            <input id="wf-path" value="${escapeAttr(builder.workflow_path || "")}" placeholder="workflows/my_workflow.yaml" />
          </label>
          <label class="cmd-field">
            <span>prompt_mode</span>
            <select id="wf-prompt-mode">
              <option value="progressive" ${promptMode === "progressive" ? "selected" : ""}>progressive</option>
              <option value="concat_stateful" ${promptMode === "concat_stateful" ? "selected" : ""}>concat_stateful</option>
              <option value="concat_blind" ${promptMode === "concat_blind" ? "selected" : ""}>concat_blind</option>
            </select>
          </label>
          <label class="cmd-field">
            <span>final_sweep_mode</span>
            <select id="wf-final-sweep-mode">
              <option value="full" ${finalSweepMode === "full" ? "selected" : ""}>full</option>
              <option value="off" ${finalSweepMode === "off" ? "selected" : ""}>off</option>
            </select>
          </label>
          <label class="cmd-field">
            <span>spec.namespaces (csv)</span>
            <input id="wf-namespaces" value="${escapeAttr(namespaces)}" placeholder="cluster_a,cluster_b" />
          </label>
        </div>
        <div class="status-line">
          <button class="button ghost" type="button" data-workflow-action="add-stage">Add Stage</button>
          <button class="button" type="button" data-workflow-action="update-draft">Update Draft</button>
          <button class="button ghost" type="button" data-workflow-action="import-yaml">Load YAML</button>
          <button class="button ghost" type="button" data-workflow-action="refresh-cli">Refresh CLI</button>
          <button class="button ghost" type="button" data-workflow-action="copy-yaml">Copy YAML</button>
        </div>
        <div class="cmd-field" style="margin-top:8px;">
          <span>Paste workflow YAML (load into builder)</span>
          <textarea id="wf-import-yaml" rows="8" placeholder="apiVersion: benchmark/v1alpha1&#10;kind: Workflow&#10;metadata:&#10;  name: my-workflow&#10;spec:&#10;  prompt_mode: progressive&#10;  stages:">${escapeHtml(builder.import_yaml_text || "")}</textarea>
        </div>
        ${builder.import_error ? `<div class="cmd-errors">${escapeHtml(builder.import_error)}</div>` : ""}
        ${builder.import_info ? `<div class="logs">${escapeHtml(builder.import_info)}</div>` : ""}
        ${validation.errors.length ? `<div class="cmd-errors">${validation.errors.map((it) => `<div>${escapeHtml(it)}</div>`).join("")}</div>` : ""}
        ${validation.warnings.length ? `<div class="cmd-warnings">${validation.warnings.map((it) => `<div>${escapeHtml(it)}</div>`).join("")}</div>` : ""}
        <div class="workflow-stage-list" id="wf-stage-list" style="margin-top:12px;">
          ${stages.map((stage, idx) => renderWorkflowBuilderStageCard(stage, idx)).join("") || '<div class="logs">No stages yet.</div>'}
        </div>
      </div>
      <div class="workflow-panel">
        <div class="section-title"><h2>YAML Preview</h2></div>
        <div class="pre" id="wf-yaml-preview">${escapeHtml(builder.yaml_preview || "")}</div>
        <div class="section-title" style="margin-top:14px;"><h2>CLI Preview</h2></div>
        ${preview.ok === false ? `<div class="cmd-errors">${escapeHtml(preview.error || "preview failed")}</div>` : ""}
        <div class="kicker">workflow-run</div>
        <div class="pre">${escapeHtml(preview.run_one_line || "(click Refresh CLI)")}</div>
        <div class="status-line">
          <button class="button ghost" type="button" data-workflow-action="copy-cli-run">Copy Run CLI</button>
        </div>
      </div>
    </section>
  `;
}

function renderWorkflowRunnerFiles() {
  const rows = (state.workflow.files || [])
    .map((wf) => {
      const status = wf.status === "invalid" ? `<span class="badge fail">INVALID</span>` : `<span class="badge">OK</span>`;
      const sub = wf.status === "invalid"
        ? `error: ${wf.error || "invalid workflow"}`
        : `${wf.stage_count || 0} stages • prompt_mode=${wf.prompt_mode || "-"}`;
      return `
        <div class="workflow-file-row">
          <div>
            <div class="workflow-file-title">${escapeHtml(wf.path || "-")}</div>
            <div class="workflow-file-sub">${escapeHtml(sub)}</div>
          </div>
          <div>${status}</div>
          <div class="status-line">
            <button class="button" data-workflow-action="start-run-debug" data-workflow-path="${escapeAttr(wf.path || "")}" ${wf.status === "invalid" ? "disabled" : ""}>Run (Debug)</button>
            <button class="button ghost" data-workflow-action="start-run-docker" data-workflow-path="${escapeAttr(wf.path || "")}" ${wf.status === "invalid" ? "disabled" : ""}>Run (Docker)</button>
          </div>
          <div>
            <button class="button ghost" data-workflow-action="open-cli" data-workflow-path="${escapeAttr(wf.path || "")}">CLI</button>
          </div>
        </div>
      `;
    })
    .join("");
  return rows || '<div class="logs">No workflow.yaml files found. Put files under <code>workflows/</code> or <code>resources/**/workflow.yaml</code>.</div>';
}

function _workflowJobLogPrefs(jobId) {
  const key = String(jobId || "");
  state.workflow.runner.logPrefs = state.workflow.runner.logPrefs || {};
  if (!state.workflow.runner.logPrefs[key]) {
    state.workflow.runner.logPrefs[key] = { autoTail: true, wrap: true, promptWrap: true };
  }
  return state.workflow.runner.logPrefs[key];
}

function _workflowJobPromptEntry(jobId) {
  const key = String(jobId || "");
  state.workflow.runner.promptCache = state.workflow.runner.promptCache || {};
  if (!state.workflow.runner.promptCache[key]) {
    state.workflow.runner.promptCache[key] = {
      request_key: "",
      fetched_key: "",
      loading: false,
      data: null,
      error: null,
    };
  }
  return state.workflow.runner.promptCache[key];
}

function _workflowPromptMetaKey(job) {
  const prompt = (job && typeof job.prompt === "object") ? job.prompt : {};
  return [
    String(job?.rev || ""),
    prompt.available ? "1" : "0",
    String(prompt.updated_at || ""),
    String(prompt.size_bytes || ""),
    String(job?.phase || ""),
  ].join("|");
}

function _workflowShouldDisplayPrompt(job) {
  const profile = String(job?.execution_profile || "").trim();
  const prompt = (job && typeof job.prompt === "object") ? job.prompt : {};
  return profile === "ui_debug_local" || !!job?.interactive_controls || !!prompt.available;
}

async function _workflowFetchJobPrompt(jobId, metaKey) {
  const entry = _workflowJobPromptEntry(jobId);
  if (entry.loading && entry.request_key === metaKey) {
    return;
  }
  if (entry.fetched_key === metaKey) {
    return;
  }
  entry.loading = true;
  entry.request_key = metaKey;
  entry.error = null;
  try {
    const payload = await fetchJSON(
      `/api/workflow/jobs/${encodeURIComponent(jobId)}/prompt?max_chars=24000`
    );
    entry.data = payload;
    entry.error = null;
  } catch (err) {
    entry.data = null;
    entry.error = err.message || String(err);
  } finally {
    entry.loading = false;
    entry.fetched_key = metaKey;
    patchWorkflowJobsDom();
  }
}

function _workflowEnsureJobPrompt(job) {
  const jobId = String(job?.id || "");
  if (!jobId) {
    return;
  }
  if (!_workflowShouldDisplayPrompt(job)) {
    return;
  }
  const entry = _workflowJobPromptEntry(jobId);
  const metaKey = _workflowPromptMetaKey(job);
  if (entry.fetched_key === metaKey || (entry.loading && entry.request_key === metaKey)) {
    return;
  }
  _workflowFetchJobPrompt(jobId, metaKey);
}

function _workflowCompactPath(value, head = 48, tail = 36) {
  const text = String(value || "-");
  if (text.length <= head + tail + 3) {
    return text;
  }
  return `${text.slice(0, head)}...${text.slice(-tail)}`;
}

function _workflowNearBottom(el, thresholdPx = 18) {
  const remaining = (el.scrollHeight - el.clientHeight) - el.scrollTop;
  return remaining <= thresholdPx;
}

function createWorkflowJobCard(jobId) {
  const card = document.createElement("div");
  card.className = "workflow-job-card";
  card.dataset.workflowJobId = String(jobId || "");
  card.innerHTML = `
    <div class="status-line" style="justify-content:space-between;">
      <div>
        <div class="workflow-job-title"></div>
        <div class="workflow-job-path"></div>
      </div>
      <div class="status-line">
        <span class="workflow-job-status"></span>
        <span class="workflow-job-kind badge"></span>
        <span class="workflow-job-mode badge"></span>
      </div>
    </div>
    <div class="workflow-job-meta">
      <div class="logs workflow-job-phase"></div>
      <div class="logs workflow-job-run-dir"></div>
      <div class="logs workflow-job-artifact"></div>
      <div class="logs workflow-job-times"></div>
    </div>
    <div class="workflow-job-controls status-line">
      <button class="button workflow-job-submit-btn" type="button" data-workflow-action="workflow-job-submit" data-workflow-job-id="${escapeAttr(jobId || "")}">Submit Stage</button>
      <button class="button ghost workflow-job-cleanup-btn" type="button" data-workflow-action="workflow-job-cleanup" data-workflow-job-id="${escapeAttr(jobId || "")}">Cleanup</button>
    </div>
    <div class="workflow-job-prompt-panel">
      <div class="workflow-job-prompt-toolbar">
        <div class="logs workflow-job-prompt-meta"></div>
        <div class="status-line">
          <button class="button ghost workflow-job-prompt-copy-btn" type="button" data-workflow-action="copy-job-prompt" data-workflow-job-id="${escapeAttr(jobId || "")}">Copy Prompt</button>
          <button class="button ghost workflow-job-prompt-wrap-btn" type="button" data-workflow-action="toggle-job-prompt-wrap" data-workflow-job-id="${escapeAttr(jobId || "")}">Wrap: On</button>
        </div>
      </div>
      <div class="pre workflow-job-prompt"></div>
    </div>
    <div class="workflow-job-log-toolbar">
      <div class="logs workflow-job-log-meta"></div>
      <div class="status-line">
        <button class="button ghost" type="button" data-workflow-action="copy-job-logs" data-workflow-job-id="${escapeAttr(jobId || "")}">Copy Logs</button>
        <button class="button ghost workflow-job-wrap-btn" type="button" data-workflow-action="toggle-job-wrap" data-workflow-job-id="${escapeAttr(jobId || "")}">Wrap: On</button>
        <button class="button ghost workflow-job-tail-btn" type="button" data-workflow-action="resume-job-tail" data-workflow-job-id="${escapeAttr(jobId || "")}">Tail On</button>
      </div>
    </div>
    <div class="pre workflow-job-logs"></div>
  `;
  const logsEl = card.querySelector(".workflow-job-logs");
  if (logsEl) {
    logsEl.addEventListener("scroll", () => {
      const prefs = _workflowJobLogPrefs(card.dataset.workflowJobId || "");
      prefs.autoTail = _workflowNearBottom(logsEl);
      const tailBtn = card.querySelector(".workflow-job-tail-btn");
      if (tailBtn) {
        tailBtn.textContent = prefs.autoTail ? "Tail On" : "Resume Tail";
        tailBtn.disabled = prefs.autoTail;
      }
    });
  }
  return card;
}

function patchWorkflowJobCard(card, job) {
  const jobId = String(job.id || card.dataset.workflowJobId || "");
  const prefs = _workflowJobLogPrefs(jobId);
  const promptKey = _workflowPromptMetaKey(job);
  _workflowEnsureJobPrompt(job);
  const title = card.querySelector(".workflow-job-title");
  if (title) {
    title.textContent = job.id || "-";
  }
  const path = card.querySelector(".workflow-job-path");
  if (path) {
    path.textContent = `${job.workflow_name || "workflow"} (${job.prompt_mode || "-"})`;
  }
  const statusEl = card.querySelector(".workflow-job-status");
  if (statusEl) {
    statusEl.innerHTML = judgeStatusBadge(job.status || "unknown");
  }
  const kind = card.querySelector(".workflow-job-kind");
  if (kind) {
    kind.textContent = String(job.kind || "-").toUpperCase();
  }
  const mode = card.querySelector(".workflow-job-mode");
  if (mode) {
    const profile = String(job.execution_profile || "").trim();
    const sandbox = String(job.sandbox_mode || "").trim().toUpperCase();
    if (profile === "ui_debug_local") {
      mode.textContent = "DEBUG LOCAL";
      mode.className = "workflow-job-mode badge warn";
    } else {
      mode.textContent = sandbox || "DEFAULT";
      mode.className = "workflow-job-mode badge";
    }
  }
  const phase = card.querySelector(".workflow-job-phase");
  if (phase) {
    const phaseMessage = String(job.phase_message || "");
    const phaseShort = phaseMessage.length > 180 ? `${phaseMessage.slice(0, 177)}...` : phaseMessage;
    phase.textContent = `phase: ${job.phase || "-"}${phaseShort ? ` • ${phaseShort}` : ""}`;
    phase.title = phaseMessage || "";
  }
  const runDir = card.querySelector(".workflow-job-run-dir");
  if (runDir) {
    const full = String(job.run_dir || "-");
    runDir.textContent = `run: ${_workflowCompactPath(full)}`;
    runDir.title = full;
  }
  const artifact = card.querySelector(".workflow-job-artifact");
  if (artifact) {
    const full = String(job.compiled_artifact_path || "-");
    artifact.textContent = `artifact: ${_workflowCompactPath(full)}`;
    artifact.title = full;
  }
  const times = card.querySelector(".workflow-job-times");
  if (times) {
    times.textContent = `started: ${job.started_at || "-"} • finished: ${job.finished_at || "-"}`;
  }
  const controlsEl = card.querySelector(".workflow-job-controls");
  const submitStageBtn = card.querySelector(".workflow-job-submit-btn");
  const cleanupBtn = card.querySelector(".workflow-job-cleanup-btn");
  const interactiveControls = !!job.interactive_controls;
  if (controlsEl) {
    controlsEl.style.display = interactiveControls ? "flex" : "none";
  }
  if (submitStageBtn) {
    submitStageBtn.disabled = !job.can_submit;
  }
  if (cleanupBtn) {
    cleanupBtn.disabled = !job.can_cleanup;
  }
  const promptPanel = card.querySelector(".workflow-job-prompt-panel");
  const promptMetaEl = card.querySelector(".workflow-job-prompt-meta");
  const promptWrapBtn = card.querySelector(".workflow-job-prompt-wrap-btn");
  const promptCopyBtn = card.querySelector(".workflow-job-prompt-copy-btn");
  const promptEl = card.querySelector(".workflow-job-prompt");
  const showPrompt = _workflowShouldDisplayPrompt(job);
  if (promptPanel) {
    promptPanel.style.display = showPrompt ? "grid" : "none";
  }
  const promptEntry = _workflowJobPromptEntry(jobId);
  const promptMeta = (job && typeof job.prompt === "object") ? job.prompt : {};
  const promptFresh = promptEntry.fetched_key === promptKey;
  let promptText = "(prompt unavailable)";
  let promptReady = false;
  let promptTruncated = false;
  let promptPath = String(promptMeta.path || "");
  let promptUpdatedAt = String(promptMeta.updated_at || "");
  let promptSize = Number(promptMeta.size_bytes || 0) || 0;
  if (promptFresh && promptEntry.error) {
    promptText = `Failed to load prompt: ${promptEntry.error}`;
  } else if (promptFresh && promptEntry.data) {
    const data = promptEntry.data;
    promptReady = !!data.available;
    promptTruncated = !!data.truncated;
    promptPath = String(data.path || promptPath || "");
    promptUpdatedAt = String(data.updated_at || promptUpdatedAt || "");
    promptSize = Number(data.size_bytes || promptSize || 0) || 0;
    if (promptReady) {
      promptText = String(data.prompt || "").trim() || "(empty prompt)";
    } else {
      const reason = String(data.reason || "");
      if (reason === "run_dir_not_ready") {
        promptText = "(prompt not ready: run dir pending)";
      } else if (reason === "prompt_not_ready") {
        promptText = "(prompt not ready yet)";
      } else {
        promptText = "(prompt unavailable)";
      }
    }
  } else if (promptEntry.loading) {
    promptText = "(loading prompt...)";
  } else if (promptMeta.available) {
    promptText = "(loading prompt...)";
  } else {
    promptText = "(prompt not ready yet)";
  }
  if (promptMetaEl) {
    const parts = ["system prompt"];
    parts.push(promptReady ? "ready" : "pending");
    if (promptPath) {
      parts.push(_workflowCompactPath(promptPath));
      promptMetaEl.title = promptPath;
    } else {
      promptMetaEl.title = "";
    }
    if (promptSize > 0) {
      parts.push(`${promptSize} bytes`);
    }
    if (promptUpdatedAt) {
      parts.push(`updated ${promptUpdatedAt}`);
    }
    if (promptTruncated) {
      parts.push("truncated");
    }
    promptMetaEl.textContent = parts.join(" • ");
  }
  if (promptWrapBtn) {
    promptWrapBtn.textContent = prefs.promptWrap ? "Wrap: On" : "Wrap: Off";
  }
  if (promptCopyBtn) {
    promptCopyBtn.disabled = !promptReady;
  }
  if (promptEl) {
    promptEl.textContent = promptText;
    promptEl.classList.toggle("no-wrap", !prefs.promptWrap);
  }
  const logMetaEl = card.querySelector(".workflow-job-log-meta");
  const wrapBtn = card.querySelector(".workflow-job-wrap-btn");
  const tailBtn = card.querySelector(".workflow-job-tail-btn");
  const stream = ((job.logs || {}).orchestrator || {});
  const lines = Array.isArray(stream.lines) ? stream.lines : [];
  const totalLines = Number(stream.total_lines || lines.length) || lines.length;
  const truncated = Number(stream.truncated || 0) || 0;
  if (logMetaEl) {
    const truncText = truncated > 0 ? ` • older ${truncated} lines trimmed` : "";
    logMetaEl.textContent = `${lines.length}/${totalLines} lines${truncText}`;
  }
  if (wrapBtn) {
    wrapBtn.textContent = prefs.wrap ? "Wrap: On" : "Wrap: Off";
  }
  if (tailBtn) {
    tailBtn.textContent = prefs.autoTail ? "Tail On" : "Resume Tail";
    tailBtn.disabled = prefs.autoTail;
  }
  const logsEl = card.querySelector(".workflow-job-logs");
  if (logsEl) {
    const bottomOffset = logsEl.scrollHeight - logsEl.scrollTop;
    logsEl.textContent = lines.join("\n") || "(no logs yet)";
    logsEl.classList.toggle("no-wrap", !prefs.wrap);
    if (prefs.autoTail) {
      logsEl.scrollTop = logsEl.scrollHeight;
    } else {
      logsEl.scrollTop = Math.max(0, logsEl.scrollHeight - bottomOffset);
    }
  }
}

function patchWorkflowJobsDom() {
  const listEl = document.getElementById("workflow-jobs-list");
  const emptyEl = document.getElementById("workflow-jobs-empty");
  if (!listEl || !emptyEl) {
    return;
  }
  const jobs = (state.workflow.runner.jobs || []).slice(0, 8);
  emptyEl.style.display = jobs.length ? "none" : "block";
  const existing = new Map();
  Array.from(listEl.children).forEach((el) => {
    const key = el.dataset ? el.dataset.workflowJobId : "";
    if (key) {
      existing.set(key, el);
    }
  });
  const ordered = [];
  for (const job of jobs) {
    const key = String(job.id || "");
    let card = existing.get(key);
    if (!card) {
      card = createWorkflowJobCard(key);
    }
    patchWorkflowJobCard(card, job);
    ordered.push(card);
    existing.delete(key);
  }
  for (const stale of existing.values()) {
    stale.remove();
  }
  listEl.replaceChildren(...ordered);
}

function renderWorkflowRunnerCliPanel() {
  const panel = state.workflow.runner.cliPanel;
  if (!panel) {
    return "";
  }
  const preview = panel.preview || {};
  return `
    <section class="section workflow-panel">
      <div class="section-title">
        <h2>Workflow CLI</h2>
        <button class="button ghost" data-workflow-action="close-cli">Close</button>
      </div>
      <div class="logs">path: ${escapeHtml(panel.workflow_path || "-")}</div>
      ${preview.ok === false ? `<div class="cmd-errors">${escapeHtml(preview.error || "preview failed")}</div>` : ""}
      <div class="kicker">workflow-run</div>
      <div class="pre">${escapeHtml(preview.run_one_line || "")}</div>
      <div class="status-line">
        <button class="button ghost" data-workflow-action="copy-panel-run">Copy Run</button>
      </div>
    </section>
  `;
}

function renderWorkflowRunnerPanel() {
  return `
    <section class="section workflow-panel">
      <div class="section-title">
        <h2>Workflow Runner</h2>
        <button class="button ghost" data-workflow-action="refresh-files">Refresh Files</button>
      </div>
      <div class="logs">Run workflow specs discovered in repo.</div>
      <div class="kicker">UI Run defaults to Debug Local for manual stage-by-stage submit. CLI preview remains docker-default.</div>
      <div id="workflow-files-list" style="margin-top:10px;">${renderWorkflowRunnerFiles()}</div>
    </section>
    <div id="workflow-cli-panel">${renderWorkflowRunnerCliPanel()}</div>
    <section class="section">
      <div class="section-title"><h2>Workflow Jobs</h2></div>
      <div id="workflow-jobs-empty" class="logs">No workflow jobs yet.</div>
      <div id="workflow-jobs-list"></div>
    </section>
  `;
}

function renderWorkflowSubviewToggle() {
  const subview = state.workflow.subview === "runner" ? "runner" : "builder";
  return `
    <section class="section workflow-subview">
      <div class="workflow-subview-toggle ${subview === "runner" ? "show-runner" : "show-builder"}" id="workflow-subview-toggle">
        <span class="workflow-subview-thumb" aria-hidden="true"></span>
        <button type="button" data-workflow-subview="builder" aria-selected="${subview === "builder" ? "true" : "false"}">Workflow Builder</button>
        <button type="button" data-workflow-subview="runner" aria-selected="${subview === "runner" ? "true" : "false"}">Workflow Runner</button>
      </div>
    </section>
  `;
}

function applyWorkflowSubviewUI() {
  const toggle = document.getElementById("workflow-subview-toggle");
  const builder = document.getElementById("workflow-subview-builder");
  const runner = document.getElementById("workflow-subview-runner");
  if (!toggle || !builder || !runner) {
    return;
  }
  const subview = state.workflow.subview === "runner" ? "runner" : "builder";
  toggle.classList.toggle("show-runner", subview === "runner");
  toggle.classList.toggle("show-builder", subview === "builder");
  toggle.querySelectorAll("[data-workflow-subview]").forEach((btn) => {
    const selected = btn.getAttribute("data-workflow-subview") === subview;
    btn.setAttribute("aria-selected", selected ? "true" : "false");
  });
  builder.style.display = subview === "builder" ? "block" : "none";
  runner.style.display = subview === "runner" ? "block" : "none";
}

function setWorkflowSubview(view) {
  state.workflow.subview = view === "runner" ? "runner" : "builder";
  applyWorkflowSubviewUI();
}

function renderWorkflowPage() {
  _workflowRefreshDerived();
  appEl.innerHTML = `
    ${renderWorkflowSubviewToggle()}
    <div id="workflow-subview-builder">
      ${renderWorkflowBuilderPanel()}
    </div>
    <div id="workflow-subview-runner">
      ${renderWorkflowRunnerPanel()}
    </div>
  `;
  attachWorkflowHandlers();
  patchWorkflowJobsDom();
  applyWorkflowSubviewUI();
}

function _workflowParseCsv(value) {
  return String(value || "")
    .split(",")
    .map((it) => it.trim())
    .filter(Boolean);
}

function _workflowParseBindings(value) {
  const out = {};
  _workflowParseCsv(value).forEach((entry) => {
    const idx = entry.indexOf(":");
    if (idx <= 0) {
      return;
    }
    const role = entry.slice(0, idx).trim();
    const alias = entry.slice(idx + 1).trim();
    if (!role || !alias) {
      return;
    }
    out[role] = alias;
  });
  return out;
}

function _workflowCollectBuilderInputs() {
  const draft = JSON.parse(JSON.stringify(state.workflow.builder.draft || {}));
  draft.metadata = draft.metadata || {};
  draft.spec = draft.spec || {};
  const nameEl = document.getElementById("wf-name");
  const modeEl = document.getElementById("wf-prompt-mode");
  const finalSweepModeEl = document.getElementById("wf-final-sweep-mode");
  const nsEl = document.getElementById("wf-namespaces");
  const pathEl = document.getElementById("wf-path");
  const importYamlEl = document.getElementById("wf-import-yaml");
  if (nameEl) {
    draft.metadata.name = nameEl.value.trim();
  }
  if (modeEl) {
    draft.spec.prompt_mode = modeEl.value;
  }
  if (finalSweepModeEl) {
    draft.spec.final_sweep_mode = String(finalSweepModeEl.value || "full").trim().toLowerCase() || "full";
  }
  if (nsEl) {
    draft.spec.namespaces = _workflowParseCsv(nsEl.value);
  }
  if (pathEl) {
    state.workflow.builder.workflow_path = pathEl.value.trim();
  }
  if (importYamlEl) {
    state.workflow.builder.import_yaml_text = importYamlEl.value;
  }
  const stageCards = Array.from(document.querySelectorAll(".workflow-stage-card"));
  const byId = new Map((draft.spec.stages || []).map((stage) => [String(stage.id || ""), stage]));
  const nextStages = [];
  for (const card of stageCards) {
    const sid = String(card.getAttribute("data-workflow-stage-id") || "");
    const prev = JSON.parse(JSON.stringify(byId.get(sid) || {}));
    const read = (field) => card.querySelector(`[data-workflow-field="${field}"]`);
    const idEl = read("id");
    const serviceEl = read("service");
    const caseEl = read("case");
    const maxEl = read("max_attempts");
    const nsStageEl = read("namespaces");
    const bindingEl = read("bindings");
    prev.id = idEl ? idEl.value.trim() : sid;
    prev.service = serviceEl ? serviceEl.value.trim() : prev.service || "";
    prev.case = caseEl ? caseEl.value.trim() : prev.case || "";
    prev.max_attempts = maxEl && maxEl.value.trim() ? Number(maxEl.value.trim()) : null;
    prev.namespaces = nsStageEl ? _workflowParseCsv(nsStageEl.value) : [];
    prev.namespace_bindings = bindingEl ? _workflowParseBindings(bindingEl.value) : {};
    const defs = _workflowCaseParamDefinitions(prev.service, prev.case);
    const paramInputs = Array.from(card.querySelectorAll('[data-workflow-field^="param:"]'));
    if (paramInputs.length) {
      const overrides = {};
      paramInputs.forEach((input) => {
        const fieldName = String(input.getAttribute("data-workflow-field") || "");
        if (!fieldName.startsWith("param:")) {
          return;
        }
        const paramName = fieldName.slice("param:".length).trim();
        if (!paramName) {
          return;
        }
        const rawValue = String(input.value || "").trim();
        if (!rawValue) {
          return;
        }
        const spec = defs && typeof defs === "object" ? defs[paramName] : null;
        overrides[paramName] = _workflowParseParamOverrideValue(rawValue, spec);
      });
      prev.param_overrides = overrides;
    } else {
      prev.param_overrides = prev.param_overrides && typeof prev.param_overrides === "object" ? prev.param_overrides : {};
    }
    nextStages.push(prev);
  }
  draft.spec.stages = nextStages;
  state.workflow.builder.draft = draft;
  state.workflow.builder.dirty = true;
}

async function loadWorkflowCatalog() {
  const catalog = {};
  await Promise.all(
    (state.services || []).map(async (svc) => {
      try {
        const data = await fetchJSON(`/api/services/${svc.name}/cases`);
        catalog[svc.name] = (data.cases || [])
          .filter((it) => it && typeof it === "object")
          .map((it) => ({
            id: String(it.id || "").trim(),
            case: String(it.case || "").trim(),
          }))
          .filter((it) => it.case);
      } catch (err) {
        catalog[svc.name] = [];
      }
    })
  );
  state.workflow.catalog = catalog;
  state.workflow.caseParams = {};
}

async function loadWorkflowFiles() {
  const data = await fetchJSON("/api/workflow/files");
  state.workflow.files = data.workflows || [];
}

async function loadWorkflowJobs() {
  const data = await fetchJSON("/api/workflow/jobs");
  state.workflow.runner.jobs = data.jobs || [];
}

async function refreshWorkflowCliPreview(pathValue) {
  const workflowPath = String(pathValue || state.workflow.builder.workflow_path || "").trim();
  if (!workflowPath) {
    state.workflow.builder.cli_preview = { ok: false, error: "workflow path is required for CLI preview" };
    return;
  }
  const payload = {
    workflow_path: workflowPath,
    flags: state.workflow.runner.flags || {},
    dry_run_run: false,
  };
  try {
    const preview = await fetchJSON("/api/workflow/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.workflow.builder.cli_preview = preview;
  } catch (err) {
    state.workflow.builder.cli_preview = { ok: false, error: err.message || String(err) };
  }
}

function _workflowApiErrorMessage(err, fallback = "request failed") {
  const raw = String((err && err.message) || err || "").trim();
  if (!raw) {
    return fallback;
  }
  const prefixMatch = raw.match(/^Request failed \(\d+\):\s*([\s\S]+)$/);
  const body = prefixMatch ? prefixMatch[1].trim() : raw;
  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed === "object" && parsed.error) {
      return String(parsed.error);
    }
  } catch (_err) {
    // Ignore JSON parse failures and fall back to the original message.
  }
  return body || raw || fallback;
}

async function importWorkflowYamlToBuilder() {
  _workflowCollectBuilderInputs();
  const builder = state.workflow.builder;
  const yamlText = String(builder.import_yaml_text || "");
  const workflowPath = String(builder.workflow_path || "").trim();
  builder.import_error = "";
  builder.import_info = "";
  if (!yamlText.trim()) {
    builder.import_error = "YAML is required";
    renderWorkflowPage();
    return;
  }
  try {
    const resp = await fetchJSON("/api/workflow/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        yaml_text: yamlText,
        workflow_path: workflowPath || "",
      }),
    });
    if (!resp || resp.ok === false || !resp.draft) {
      builder.import_error = String((resp && resp.error) || "workflow import failed");
      renderWorkflowPage();
      return;
    }
    builder.draft = resp.draft;
    builder.expanded_stage_id = null;
    builder.param_ref_expanded = {};
    builder.import_error = "";
    builder.import_info = `Loaded ${resp.stage_count || 0} stage(s) from YAML`;
    builder.dirty = true;
    await _workflowPrimeDraftCaseParams(builder.draft);
    await refreshWorkflowCliPreview();
    renderWorkflowPage();
  } catch (err) {
    builder.import_error = _workflowApiErrorMessage(err, "workflow import failed");
    builder.import_info = "";
    renderWorkflowPage();
  }
}

function upsertWorkflowJob(job) {
  if (!job || !job.id) {
    return;
  }
  const idx = state.workflow.runner.jobs.findIndex((it) => it.id === job.id);
  if (idx >= 0) {
    state.workflow.runner.jobs[idx] = job;
  } else {
    state.workflow.runner.jobs.unshift(job);
  }
}

function applyWorkflowLogEvent(payload) {
  if (!payload || !payload.job_id) {
    return;
  }
  const idx = state.workflow.runner.jobs.findIndex((it) => it.id === payload.job_id);
  if (idx < 0) {
    return;
  }
  const job = state.workflow.runner.jobs[idx];
  const lines = ((((job.logs || {}).orchestrator || {}).lines || []).slice());
  const delta = Array.isArray(payload.lines) ? payload.lines.map((it) => String(it)) : [];
  if (delta.length) {
    lines.push(...delta);
  }
  const maxLines = 300;
  const nextLines = lines.length > maxLines ? lines.slice(-maxLines) : lines;
  const nextJob = JSON.parse(JSON.stringify(job));
  nextJob.logs = nextJob.logs || {};
  nextJob.logs.orchestrator = nextJob.logs.orchestrator || {};
  nextJob.logs.orchestrator.lines = nextLines;
  nextJob.logs.orchestrator.total_lines = payload.total_lines || lines.length;
  nextJob.logs.orchestrator.truncated = Math.max(0, lines.length - nextLines.length);
  state.workflow.runner.jobs[idx] = nextJob;
}

function applyWorkflowPhaseEvent(payload) {
  if (!payload || !payload.job_id) {
    return;
  }
  const idx = state.workflow.runner.jobs.findIndex((it) => it.id === payload.job_id);
  if (idx < 0) {
    return;
  }
  const job = JSON.parse(JSON.stringify(state.workflow.runner.jobs[idx]));
  if (payload.phase) {
    job.phase = payload.phase;
  }
  if (payload.phase_message !== undefined) {
    job.phase_message = payload.phase_message;
  }
  if (payload.rev !== undefined) {
    job.rev = payload.rev;
  }
  if (payload.can_submit !== undefined) {
    job.can_submit = !!payload.can_submit;
  }
  if (payload.can_cleanup !== undefined) {
    job.can_cleanup = !!payload.can_cleanup;
  }
  if (payload.interactive_controls !== undefined) {
    job.interactive_controls = !!payload.interactive_controls;
  }
  if (payload.prompt !== undefined) {
    job.prompt = payload.prompt || {};
  }
  state.workflow.runner.jobs[idx] = job;
}

function stopWorkflowStream() {
  const stream = state.workflow.runner.stream;
  if (stream) {
    try {
      stream.close();
    } catch (err) {
      console.error(err);
    }
    state.workflow.runner.stream = null;
  }
}

function startWorkflowPolling() {
  if (state.workflow.runner.stream && !state.workflow.runner.streamFallback) {
    return;
  }
  if (state.workflow.runner.pollTimer) {
    return;
  }
  state.workflow.runner.pollTick = 0;
  state.workflow.runner.pollTimer = setInterval(async () => {
    if (state.viewMode !== "workflow") {
      return;
    }
    try {
      state.workflow.runner.pollTick += 1;
      await loadWorkflowJobs();
      patchWorkflowJobsDom();
      const hasRunning = state.workflow.runner.jobs.some((it) => (it.status || "").toLowerCase() === "running");
      if (hasRunning || state.workflow.runner.pollTick % 6 === 0) {
        await loadWorkflowFiles();
        const files = document.getElementById("workflow-files-list");
        if (files) {
          files.innerHTML = renderWorkflowRunnerFiles();
        }
      }
    } catch (err) {
      console.error(err);
    }
  }, 2500);
}

function stopWorkflowPolling() {
  if (state.workflow.runner.pollTimer) {
    clearInterval(state.workflow.runner.pollTimer);
    state.workflow.runner.pollTimer = null;
  }
}

function startWorkflowStream() {
  if (typeof EventSource === "undefined") {
    state.workflow.runner.streamFallback = true;
    startWorkflowPolling();
    return;
  }
  if (state.workflow.runner.stream) {
    return;
  }
  const since = Number(state.workflow.runner.streamSeq || 0);
  const url = `/api/workflow/stream?since=${encodeURIComponent(since)}`;
  let stream;
  try {
    stream = new EventSource(url);
  } catch (err) {
    console.error(err);
    state.workflow.runner.streamFallback = true;
    startWorkflowPolling();
    return;
  }
  state.workflow.runner.stream = stream;
  state.workflow.runner.streamFallback = false;
  stream.onopen = () => {
    state.workflow.runner.streamFallback = false;
    stopWorkflowPolling();
  };

  stream.addEventListener("hello", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      state.workflow.runner.jobs = payload.jobs || [];
      state.workflow.runner.streamSeq = Number(payload.seq || state.workflow.runner.streamSeq || 0);
      patchWorkflowJobsDom();
    } catch (err) {
      console.error(err);
    }
  });
  stream.addEventListener("job_upsert", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      if (payload && payload.job) {
        upsertWorkflowJob(payload.job);
      }
      const seq = Number(event.lastEventId || 0);
      if (seq > 0) {
        state.workflow.runner.streamSeq = seq;
      }
      patchWorkflowJobsDom();
    } catch (err) {
      console.error(err);
    }
  });
  stream.addEventListener("log_append", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      applyWorkflowLogEvent(payload);
      const seq = Number(event.lastEventId || 0);
      if (seq > 0) {
        state.workflow.runner.streamSeq = seq;
      }
      patchWorkflowJobsDom();
    } catch (err) {
      console.error(err);
    }
  });
  stream.addEventListener("job_phase", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      applyWorkflowPhaseEvent(payload);
      const seq = Number(event.lastEventId || 0);
      if (seq > 0) {
        state.workflow.runner.streamSeq = seq;
      }
      patchWorkflowJobsDom();
    } catch (err) {
      console.error(err);
    }
  });
  stream.addEventListener("invalidate_workflow_files", async (event) => {
    try {
      const seq = Number(event.lastEventId || 0);
      if (seq > 0) {
        state.workflow.runner.streamSeq = seq;
      }
      await loadWorkflowFiles();
      const files = document.getElementById("workflow-files-list");
      if (files) {
        files.innerHTML = renderWorkflowRunnerFiles();
      }
    } catch (err) {
      console.error(err);
    }
  });
  stream.addEventListener("heartbeat", (event) => {
    const seq = Number(event.lastEventId || 0);
    if (seq > 0) {
      state.workflow.runner.streamSeq = seq;
    }
  });
  stream.onerror = () => {
    stopWorkflowStream();
    if (state.viewMode !== "workflow") {
      return;
    }
    if (!state.workflow.runner.streamFallback) {
      state.workflow.runner.streamFallback = true;
      startWorkflowPolling();
    }
  };
}

async function _workflowStartAction(action, workflowPath, executionMode = "") {
  if (!workflowPath) {
    alert("workflow path is required");
    return;
  }
  let actionLabel = "Run";
  if (String(executionMode || "").toLowerCase() === "debug") {
    actionLabel = "Run (Debug)";
  } else if (String(executionMode || "").toLowerCase() === "docker") {
    actionLabel = "Run (Docker)";
  }
  const ok = confirm(`${actionLabel} workflow ${workflowPath}?`);
  if (!ok) {
    return;
  }
  const requestBody = {
    action,
    workflow_path: workflowPath,
    flags: state.workflow.runner.flags || {},
    source: "ui",
  };
  if (action === "run" && executionMode) {
    requestBody.execution_mode = String(executionMode || "").toLowerCase();
  }
  try {
    const resp = await fetchJSON("/api/workflow/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    if (resp.error) {
      alert(resp.error);
      return;
    }
  } catch (err) {
    alert(err.message || String(err));
    return;
  }
  await loadWorkflowJobs();
  patchWorkflowJobsDom();
}

async function _workflowControlAction(jobId, controlAction) {
  const targetId = String(jobId || "").trim();
  if (!targetId) {
    alert("workflow job id is required");
    return;
  }
  const action = String(controlAction || "").trim().toLowerCase() === "cleanup" ? "cleanup" : "submit";
  const endpoint = `/api/workflow/jobs/${encodeURIComponent(targetId)}/${action}`;
  try {
    const resp = await fetchJSON(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (resp.error) {
      alert(resp.error);
      return;
    }
  } catch (err) {
    alert(err.message || String(err));
    return;
  }
  await loadWorkflowJobs();
  patchWorkflowJobsDom();
}

async function _workflowOpenCliPanel(workflowPath) {
  state.workflow.runner.cliPanel = {
    workflow_path: workflowPath,
    preview: null,
  };
  try {
    const preview = await fetchJSON("/api/workflow/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workflow_path: workflowPath,
          flags: state.workflow.runner.flags || {},
        }),
    });
    state.workflow.runner.cliPanel.preview = preview;
  } catch (err) {
    state.workflow.runner.cliPanel.preview = { ok: false, error: err.message || String(err) };
  }
  const panel = document.getElementById("workflow-cli-panel");
  if (panel) {
    panel.innerHTML = renderWorkflowRunnerCliPanel();
  }
}

function attachWorkflowHandlers() {
  if (state.workflow.handlersAttached) {
    return;
  }
  appEl.addEventListener("click", async (event) => {
    if (state.viewMode !== "workflow" || !(event.target instanceof Element)) {
      return;
    }
    const subviewBtn = event.target.closest("[data-workflow-subview]");
    if (subviewBtn) {
      setWorkflowSubview(subviewBtn.getAttribute("data-workflow-subview"));
      return;
    }
    const actionEl = event.target.closest("[data-workflow-action]");
    if (!actionEl) {
      const stageToggleEl = event.target.closest("[data-workflow-stage-toggle]");
      if (stageToggleEl) {
        const card = stageToggleEl.closest(".workflow-stage-card");
        const stageId = String(card?.getAttribute("data-workflow-stage-id") || "");
        if (stageId) {
          _workflowCollectBuilderInputs();
          const current = String(state.workflow.builder.expanded_stage_id || "");
          state.workflow.builder.expanded_stage_id = current === stageId ? null : stageId;
          renderWorkflowPage();
        }
      }
      return;
    }
    const action = actionEl.getAttribute("data-workflow-action");
    const stageId = actionEl.getAttribute("data-workflow-stage-id");
    const wfPath = actionEl.getAttribute("data-workflow-path");
    if (action === "add-stage") {
      _workflowCollectBuilderInputs();
      const nextStage = _workflowDefaultStage();
      state.workflow.builder.draft.spec.stages.push(nextStage);
      state.workflow.builder.expanded_stage_id = String(nextStage.id || "");
      await _workflowPrimeDraftCaseParams(state.workflow.builder.draft);
      renderWorkflowPage();
      return;
    }
    if (action === "update-draft") {
      _workflowCollectBuilderInputs();
      await _workflowPrimeDraftCaseParams(state.workflow.builder.draft);
      renderWorkflowPage();
      return;
    }
    if (action === "refresh-cli") {
      _workflowCollectBuilderInputs();
      await _workflowPrimeDraftCaseParams(state.workflow.builder.draft);
      await refreshWorkflowCliPreview();
      renderWorkflowPage();
      return;
    }
    if (action === "import-yaml") {
      await importWorkflowYamlToBuilder();
      return;
    }
    if (action === "copy-yaml") {
      await copyText(state.workflow.builder.yaml_preview || "");
      alert("Copied workflow YAML preview");
      return;
    }
    if (action === "copy-cli-run") {
      await copyText(state.workflow.builder.cli_preview?.run_one_line || "");
      alert("Copied workflow-run command");
      return;
    }
    if (action === "toggle-param-ref") {
      _workflowCollectBuilderInputs();
      const paramName = String(actionEl.getAttribute("data-workflow-param-name") || "").trim();
      if (!stageId || !paramName) {
        return;
      }
      const card = actionEl.closest(".workflow-stage-card");
      const paramInput = card
        ? card.querySelector(`[data-workflow-field="param:${cssEscape(paramName)}"][data-workflow-stage-id="${cssEscape(stageId)}"]`)
        : null;
      const currentValue = String(paramInput?.value || "");
      const expanded = _workflowIsParamRefExpanded(stageId, paramName, currentValue);
      _workflowSetParamRefExpanded(stageId, paramName, !expanded);
      renderWorkflowPage();
      return;
    }
    if (action === "remove-stage") {
      _workflowCollectBuilderInputs();
      state.workflow.builder.draft.spec.stages = (state.workflow.builder.draft.spec.stages || []).filter((it) => String(it.id || "") !== String(stageId || ""));
      _workflowDropStageParamRefUiState(stageId);
      if (String(state.workflow.builder.expanded_stage_id || "") === String(stageId || "")) {
        state.workflow.builder.expanded_stage_id = null;
      }
      renderWorkflowPage();
      return;
    }
    if (action === "toggle-stage") {
      _workflowCollectBuilderInputs();
      const current = String(state.workflow.builder.expanded_stage_id || "");
      const target = String(stageId || "");
      state.workflow.builder.expanded_stage_id = current === target ? null : target;
      renderWorkflowPage();
      return;
    }
    if (action === "refresh-files") {
      await loadWorkflowFiles();
      const files = document.getElementById("workflow-files-list");
      if (files) {
        files.innerHTML = renderWorkflowRunnerFiles();
      }
      return;
    }
    if (action === "start-run" || action === "start-run-debug") {
      await _workflowStartAction("run", wfPath, "debug");
      return;
    }
    if (action === "start-run-docker") {
      await _workflowStartAction("run", wfPath, "docker");
      return;
    }
    if (action === "workflow-job-submit") {
      const jobId = String(actionEl.getAttribute("data-workflow-job-id") || "");
      await _workflowControlAction(jobId, "submit");
      return;
    }
    if (action === "workflow-job-cleanup") {
      const jobId = String(actionEl.getAttribute("data-workflow-job-id") || "");
      await _workflowControlAction(jobId, "cleanup");
      return;
    }
    if (action === "open-cli") {
      await _workflowOpenCliPanel(wfPath);
      return;
    }
    if (action === "close-cli") {
      state.workflow.runner.cliPanel = null;
      const panel = document.getElementById("workflow-cli-panel");
      if (panel) {
        panel.innerHTML = renderWorkflowRunnerCliPanel();
      }
      return;
    }
    if (action === "copy-panel-run") {
      await copyText(state.workflow.runner.cliPanel?.preview?.run_one_line || "");
      alert("Copied workflow-run command");
      return;
    }
    if (action === "copy-job-logs") {
      const jobId = String(actionEl.getAttribute("data-workflow-job-id") || "");
      const job = (state.workflow.runner.jobs || []).find((it) => String(it.id || "") === jobId);
      const lines = (((job || {}).logs || {}).orchestrator || {}).lines || [];
      await copyText((lines || []).join("\n") || "(no logs yet)");
      alert("Copied workflow logs");
      return;
    }
    if (action === "copy-job-prompt") {
      const jobId = String(actionEl.getAttribute("data-workflow-job-id") || "");
      const job = (state.workflow.runner.jobs || []).find((it) => String(it.id || "") === jobId);
      const entry = _workflowJobPromptEntry(jobId);
      const fresh = entry.fetched_key === _workflowPromptMetaKey(job || {});
      const prompt = fresh ? String((entry.data || {}).prompt || "") : "";
      if (!prompt.trim()) {
        alert("Prompt is not available yet");
        return;
      }
      await copyText(prompt);
      alert("Copied workflow prompt");
      return;
    }
    if (action === "toggle-job-prompt-wrap") {
      const jobId = String(actionEl.getAttribute("data-workflow-job-id") || "");
      const prefs = _workflowJobLogPrefs(jobId);
      prefs.promptWrap = !prefs.promptWrap;
      patchWorkflowJobsDom();
      return;
    }
    if (action === "toggle-job-wrap") {
      const jobId = String(actionEl.getAttribute("data-workflow-job-id") || "");
      const prefs = _workflowJobLogPrefs(jobId);
      prefs.wrap = !prefs.wrap;
      patchWorkflowJobsDom();
      return;
    }
    if (action === "resume-job-tail") {
      const jobId = String(actionEl.getAttribute("data-workflow-job-id") || "");
      const prefs = _workflowJobLogPrefs(jobId);
      prefs.autoTail = true;
      patchWorkflowJobsDom();
      return;
    }
  });

  appEl.addEventListener("change", async (event) => {
    if (state.viewMode !== "workflow" || !(event.target instanceof Element)) {
      return;
    }
    const refStageEl = event.target.closest("[data-workflow-param-ref-stage]");
    if (refStageEl) {
      const stageId = String(refStageEl.getAttribute("data-workflow-stage-id") || "");
      const paramName = String(refStageEl.getAttribute("data-workflow-param-ref-stage") || "");
      const card = refStageEl.closest(".workflow-stage-card");
      _workflowUpdateParamRefSelectOptions(card, stageId, paramName);
      _workflowApplyParamRefSelection(card, stageId, paramName);
      return;
    }
    const refParamEl = event.target.closest("[data-workflow-param-ref-param]");
    if (refParamEl) {
      const stageId = String(refParamEl.getAttribute("data-workflow-stage-id") || "");
      const paramName = String(refParamEl.getAttribute("data-workflow-param-ref-param") || "");
      const card = refParamEl.closest(".workflow-stage-card");
      _workflowApplyParamRefSelection(card, stageId, paramName);
      return;
    }
    const workflowField = event.target.closest("[data-workflow-field]");
    if (!workflowField) {
      return;
    }
    const fieldName = workflowField.getAttribute("data-workflow-field");
    if (fieldName !== "service" && fieldName !== "case") {
      return;
    }
    const stageId = workflowField.getAttribute("data-workflow-stage-id");
    if (fieldName === "service") {
      const service = workflowField.value;
      const caseField = document.querySelector(
        `[data-workflow-field="case"][data-workflow-stage-id="${cssEscape(stageId || "")}"]`
      );
      if (caseField) {
        caseField.innerHTML = _workflowRenderCaseOptions(service, "");
      }
    }
    _workflowCollectBuilderInputs();
    const stages = state.workflow.builder.draft.spec.stages || [];
    const stage = stages.find((it) => String(it.id || "") === String(stageId || ""));
    if (stage) {
      await _workflowEnsureCaseParams(stage.service, stage.case);
      _workflowPruneStageParamOverrides(stage);
    }
    renderWorkflowPage();
  });

  appEl.addEventListener("dragstart", (event) => {
    if (state.viewMode !== "workflow" || !(event.target instanceof Element)) {
      return;
    }
    const card = event.target.closest(".workflow-stage-card");
    if (!card) {
      return;
    }
    const stageId = card.getAttribute("data-workflow-stage-id");
    state.workflow.builder.drag.dragging_stage_id = stageId;
    card.classList.add("dragging");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", stageId || "");
    }
  });

  appEl.addEventListener("dragend", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const card = event.target.closest(".workflow-stage-card");
    if (card) {
      card.classList.remove("dragging");
    }
    state.workflow.builder.drag.dragging_stage_id = null;
    state.workflow.builder.drag.over_index = null;
  });

  appEl.addEventListener("dragover", (event) => {
    if (state.viewMode !== "workflow" || !(event.target instanceof Element)) {
      return;
    }
    const card = event.target.closest(".workflow-stage-card");
    if (!card) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "move";
    }
  });

  appEl.addEventListener("drop", (event) => {
    if (state.viewMode !== "workflow" || !(event.target instanceof Element)) {
      return;
    }
    const card = event.target.closest(".workflow-stage-card");
    if (!card) {
      return;
    }
    event.preventDefault();
    const toId = card.getAttribute("data-workflow-stage-id");
    const fromId = state.workflow.builder.drag.dragging_stage_id;
    if (!fromId || !toId || fromId === toId) {
      return;
    }
    _workflowCollectBuilderInputs();
    const stages = state.workflow.builder.draft.spec.stages || [];
    const from = stages.findIndex((it) => String(it.id || "") === String(fromId));
    const to = stages.findIndex((it) => String(it.id || "") === String(toId));
    if (from < 0 || to < 0) {
      return;
    }
    const item = stages.splice(from, 1)[0];
    stages.splice(to, 0, item);
    renderWorkflowPage();
  });

  state.workflow.handlersAttached = true;
}

function judgeStatusBadge(status) {
  const raw = (status || "unknown").toString();
  const label = raw.replace(/_/g, " ").toUpperCase();
  let cls = "badge";
  if (raw.includes("fail") || raw === "error") {
    cls += " fail";
  } else if (raw === "running") {
    cls += " warn";
  }
  return `<span class="${cls}">${escapeHtml(label)}</span>`;
}

function renderJudgeRowDetails(row) {
  const result = row.judge_result_path ? `<div>result: ${escapeHtml(row.judge_result_path)}</div>` : "";
  const summary = row.judge_summary_path ? `<div>summary: ${escapeHtml(row.judge_summary_path)}</div>` : "";
  const started = row.started_at ? `<div>started: ${escapeHtml(row.started_at)}</div>` : "";
  const judged = row.judge_evaluated_at ? `<div>judged: ${escapeHtml(row.judge_evaluated_at)}</div>` : "";
  return `
    <details class="judge-details">
      <summary>details</summary>
      <div class="logs">
        ${started}
        ${judged}
        ${result}
        ${summary}
      </div>
    </details>
  `;
}

function renderBatchRowDetails(row) {
  const generated = row.judge_generated_at ? `<div>generated: ${escapeHtml(row.judge_generated_at)}</div>` : "";
  const summary = row.judge_summary_path ? `<div>summary: ${escapeHtml(row.judge_summary_path)}</div>` : "";
  return `
    <details class="judge-details">
      <summary>details</summary>
      <div class="logs">
        ${generated}
        ${summary}
      </div>
    </details>
  `;
}

function renderJudgeCliPanel() {
  const panel = state.judge.cliPanel;
  if (!panel) {
    return "";
  }
  const preview = panel.preview || {};
  const command = preview.command_one_line || "(click Refresh)";
  return `
    <section class="section judge-cli">
      <div class="section-title">
        <h2>Judge CLI</h2>
        <button class="button ghost" id="judge-cli-close">Close</button>
      </div>
      <div class="judge-grid">
        <label class="judge-field">
          <span>Target Type</span>
          <input value="${escapeAttr(panel.target_type)}" disabled />
        </label>
        <label class="judge-field">
          <span>Target Path</span>
          <input value="${escapeAttr(panel.target_path)}" disabled />
        </label>
        <label class="judge-field">
          <span>Judge Env File (optional)</span>
          <input id="judge-cli-env" value="${escapeAttr(panel.judge_env_file || "")}" placeholder="judge.env" />
        </label>
        <label class="judge-field">
          <span>Dry Run</span>
          <input id="judge-cli-dry-run" type="checkbox" ${panel.dry_run ? "checked" : ""} />
        </label>
      </div>
      ${preview.ok === false ? `<div class="cmd-errors">${escapeHtml(preview.error || "preview failed")}</div>` : ""}
      <div class="kicker">One-line</div>
      <div class="pre">${escapeHtml(command)}</div>
      <div class="status-line">
        <button class="button ghost" id="judge-cli-refresh">Refresh</button>
        <button class="button ghost" id="judge-cli-copy">Copy Command</button>
      </div>
    </section>
  `;
}

function renderJudgeJobs() {
  return `
    <section class="section">
      <div class="section-title"><h2>Judge Jobs</h2></div>
      <div id="judge-jobs-empty" class="logs">No judge jobs yet.</div>
      <div id="judge-jobs-list"></div>
    </section>
  `;
}

function createJudgeJobCard(jobId) {
  const card = document.createElement("div");
  card.className = "job-card";
  card.dataset.jobId = String(jobId || "");
  card.innerHTML = `
    <div class="status-line">
      <strong class="job-id"></strong>
      <span class="job-status"></span>
      <span class="logs job-target"></span>
    </div>
    <div class="table-wrap job-progress-wrap" style="margin-top:10px; display:none;">
      <table class="judge-table">
        <thead><tr><th>Run</th><th>Status</th><th>Score</th></tr></thead>
        <tbody class="job-progress-body"></tbody>
      </table>
    </div>
    <div class="pre job-logs" style="margin-top:10px;"></div>
  `;
  return card;
}

function renderJudgeProgressRows(job) {
  return (job.progress || [])
    .map(
      (item) => `<tr>
        <td class="judge-cell">${escapeHtml(item.label || "-")}</td>
        <td>${judgeStatusBadge(item.status || "")}</td>
        <td>${item.score === null || item.score === undefined ? "-" : escapeHtml(String(item.score))}</td>
      </tr>`
    )
    .join("");
}

function patchJudgeJobCard(card, job) {
  const idEl = card.querySelector(".job-id");
  if (idEl) {
    idEl.textContent = job.id || "-";
  }
  const statusEl = card.querySelector(".job-status");
  if (statusEl) {
    statusEl.innerHTML = judgeStatusBadge(job.status || "");
  }
  const targetEl = card.querySelector(".job-target");
  if (targetEl) {
    targetEl.textContent = `${job.target_type || ""}: ${job.target_path || ""}`;
  }
  const progressRows = renderJudgeProgressRows(job);
  const progressWrap = card.querySelector(".job-progress-wrap");
  const progressBody = card.querySelector(".job-progress-body");
  if (progressWrap && progressBody) {
    progressBody.innerHTML = progressRows;
    progressWrap.style.display = progressRows ? "block" : "none";
  }
  const logs = (job.log_lines || []).slice(-10).join("\n");
  const logsEl = card.querySelector(".job-logs");
  if (logsEl) {
    logsEl.textContent = logs || "(no logs yet)";
  }
}

function patchJudgeJobsDom() {
  const listEl = document.getElementById("judge-jobs-list");
  const emptyEl = document.getElementById("judge-jobs-empty");
  if (!listEl || !emptyEl) {
    return;
  }

  const jobs = (state.judge.jobs || []).slice(0, 3);
  emptyEl.style.display = jobs.length ? "none" : "block";

  const existing = new Map();
  Array.from(listEl.children).forEach((el) => {
    const key = el.dataset ? el.dataset.jobId : "";
    if (key) {
      existing.set(key, el);
    }
  });

  const ordered = [];
  for (const job of jobs) {
    const key = String(job.id || "");
    let card = existing.get(key);
    if (!card) {
      card = createJudgeJobCard(key);
    }
    patchJudgeJobCard(card, job);
    ordered.push(card);
    existing.delete(key);
  }

  for (const stale of existing.values()) {
    stale.remove();
  }
  listEl.replaceChildren(...ordered);
}

function renderJudgeRunsRows() {
  const runRows = (state.judge.runs || [])
    .map((row) => {
      const label = `${row.service || "-"}/${row.case || "-"}`;
      return `
        <tr>
          <td class="judge-cell">${escapeHtml(row.run_dir || "-")}${renderJudgeRowDetails(row)}</td>
          <td>${escapeHtml(label)}</td>
          <td>${judgeStatusBadge(row.status || "unknown")}</td>
          <td>${judgeStatusBadge(row.judge_status || "not_judged")}</td>
          <td>${row.judge_score === null || row.judge_score === undefined ? "-" : escapeHtml(String(row.judge_score))}</td>
          <td>
            <div class="judge-actions">
              <button class="button ghost" data-judge-action="start" data-target-type="run" data-target-path="${escapeAttr(row.run_dir || "")}" data-dry-run="1">Dry Run</button>
              <button class="button" data-judge-action="start" data-target-type="run" data-target-path="${escapeAttr(row.run_dir || "")}" data-dry-run="0">Judge</button>
              <button class="button ghost" data-judge-action="cli" data-target-type="run" data-target-path="${escapeAttr(row.run_dir || "")}" data-dry-run="1">CLI</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
  return runRows || '<tr><td colspan="6" class="logs">No runs found.</td></tr>';
}

function renderJudgeBatchRows() {
  const batchRows = (state.judge.batches || [])
    .map((row) => {
      return `
        <tr>
          <td class="judge-cell">${escapeHtml(row.batch_dir || "-")}${renderBatchRowDetails(row)}</td>
          <td>${escapeHtml(String(row.run_count ?? "-"))}</td>
          <td>${escapeHtml(String(row.judged_count ?? "-"))}</td>
          <td>${row.average_final_score === null || row.average_final_score === undefined ? "-" : escapeHtml(String(row.average_final_score))}</td>
          <td>
            <div class="judge-actions">
              <button class="button ghost" data-judge-action="start" data-target-type="batch" data-target-path="${escapeAttr(row.batch_dir || "")}" data-dry-run="1">Dry Run Batch</button>
              <button class="button" data-judge-action="start" data-target-type="batch" data-target-path="${escapeAttr(row.batch_dir || "")}" data-dry-run="0">Judge Batch</button>
              <button class="button ghost" data-judge-action="cli" data-target-type="batch" data-target-path="${escapeAttr(row.batch_dir || "")}" data-dry-run="1">CLI</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
  return batchRows || '<tr><td colspan="5" class="logs">No batches found.</td></tr>';
}

function renderJudgeSubviewToggle() {
  const view = state.judge.subview === "runs" ? "runs" : "batches";
  return `
    <section class="section judge-subview">
      <div class="judge-subview-toggle ${view === "runs" ? "show-runs" : "show-batches"}" id="judge-subview-toggle" role="tablist" aria-label="Judge table view">
        <span class="judge-subview-thumb" aria-hidden="true"></span>
        <button type="button" role="tab" data-judge-subview="batches" aria-selected="${view === "batches" ? "true" : "false"}">Batches</button>
        <button type="button" role="tab" data-judge-subview="runs" aria-selected="${view === "runs" ? "true" : "false"}">Single Runs</button>
      </div>
    </section>
  `;
}

function applyJudgeSubviewUI() {
  const toggle = document.getElementById("judge-subview-toggle");
  const batchesSection = document.getElementById("judge-section-batches");
  const runsSection = document.getElementById("judge-section-runs");
  if (!toggle || !batchesSection || !runsSection) {
    return;
  }
  const view = state.judge.subview === "runs" ? "runs" : "batches";
  toggle.classList.toggle("show-runs", view === "runs");
  toggle.classList.toggle("show-batches", view === "batches");
  toggle.querySelectorAll("[data-judge-subview]").forEach((btn) => {
    const selected = btn.getAttribute("data-judge-subview") === view;
    btn.setAttribute("aria-selected", selected ? "true" : "false");
  });
  batchesSection.style.display = view === "batches" ? "block" : "none";
  runsSection.style.display = view === "runs" ? "block" : "none";
}

function setJudgeSubview(view) {
  const next = view === "runs" ? "runs" : "batches";
  state.judge.subview = next;
  try {
    window.localStorage.setItem("judge-subview", next);
  } catch (err) {
    console.error(err);
  }
  applyJudgeSubviewUI();
}

function updateJudgePageSections(options = {}) {
  const { runs = true, batches = true, jobs = true, cli = false } = options;
  const runBody = document.getElementById("judge-runs-body");
  if (!runBody) {
    renderJudgePage();
    return;
  }
  if (runs) {
    runBody.innerHTML = renderJudgeRunsRows();
  }
  if (batches) {
    const batchBody = document.getElementById("judge-batches-body");
    if (batchBody) {
      batchBody.innerHTML = renderJudgeBatchRows();
    }
  }
  if (jobs) {
    patchJudgeJobsDom();
  }
  if (cli) {
    const cliContainer = document.getElementById("judge-cli-container");
    if (cliContainer) {
      cliContainer.innerHTML = renderJudgeCliPanel();
    }
  }
}

function renderJudgePage() {
  appEl.innerHTML = `
    <section class="section">
      <div class="logs">Running judge overwrites each selected run's <code>judge/</code> folder.</div>
    </section>
    <div id="judge-cli-container">${renderJudgeCliPanel()}</div>
    ${renderJudgeSubviewToggle()}
    <section class="section" id="judge-section-batches">
      <div class="section-title"><h2>Batches</h2></div>
      <div class="table-wrap">
        <table class="judge-table">
          <thead>
            <tr><th>Batch Dir</th><th>Runs</th><th>Judged</th><th>Avg Score</th><th>Actions</th></tr>
          </thead>
          <tbody id="judge-batches-body">${renderJudgeBatchRows()}</tbody>
        </table>
      </div>
    </section>
    <section class="section" id="judge-section-runs">
      <div class="section-title"><h2>Single Runs</h2></div>
      <div class="table-wrap">
        <table class="judge-table">
          <thead>
            <tr><th>Run Dir</th><th>Service/Case</th><th>Run</th><th>Judge</th><th>Score</th><th>Actions</th></tr>
          </thead>
          <tbody id="judge-runs-body">${renderJudgeRunsRows()}</tbody>
        </table>
      </div>
    </section>
    <div id="judge-jobs-container">${renderJudgeJobs()}</div>
  `;
  attachJudgeHandlers();
  patchJudgeJobsDom();
  applyJudgeSubviewUI();
}

async function loadJudgeRunsAndBatches() {
  const [runsData, batchesData] = await Promise.all([
    fetchJSON("/api/judge/runs"),
    fetchJSON("/api/judge/batches"),
  ]);
  state.judge.runs = runsData.runs || [];
  state.judge.batches = batchesData.batches || [];
}

async function loadJudgeJobs() {
  const jobsData = await fetchJSON("/api/judge/jobs");
  state.judge.jobs = jobsData.jobs || [];
  return state.judge.jobs;
}

async function loadJudgeData() {
  await Promise.all([loadJudgeRunsAndBatches(), loadJudgeJobs()]);
}

function startJudgePolling() {
  if (state.judge.stream && !state.judge.streamFallback) {
    return;
  }
  if (state.judge.pollTimer) {
    return;
  }
  state.judge.pollTick = 0;
  state.judge.pollTimer = setInterval(async () => {
    if (state.viewMode !== "judge") {
      return;
    }
    try {
      state.judge.pollTick += 1;
      const jobs = await loadJudgeJobs();
      updateJudgePageSections({ runs: false, batches: false, jobs: true, cli: false });
      const hasRunningJobs = jobs.some((job) => (job.status || "").toLowerCase() === "running");
      if (hasRunningJobs || state.judge.pollTick % 6 === 0) {
        await loadJudgeRunsAndBatches();
        updateJudgePageSections({ runs: true, batches: true, jobs: false, cli: false });
      }
    } catch (err) {
      console.error(err);
    }
  }, 2500);
}

function stopJudgePolling() {
  if (state.judge.pollTimer) {
    clearInterval(state.judge.pollTimer);
    state.judge.pollTimer = null;
  }
}

function upsertJudgeJob(job) {
  if (!job || !job.id) {
    return;
  }
  const idx = state.judge.jobs.findIndex((it) => it.id === job.id);
  if (idx >= 0) {
    state.judge.jobs[idx] = job;
  } else {
    state.judge.jobs.unshift(job);
  }
}

function applyJudgeProgressEvent(payload) {
  if (!payload || !payload.job_id || !payload.progress) {
    return;
  }
  const idx = state.judge.jobs.findIndex((it) => it.id === payload.job_id);
  if (idx < 0) {
    return;
  }
  const job = state.judge.jobs[idx];
  const progress = payload.progress || {};
  const key = progress.label || "";
  if (!key) {
    return;
  }
  const current = Array.isArray(job.progress) ? [...job.progress] : [];
  const pIdx = current.findIndex((it) => (it?.label || "") === key);
  if (pIdx >= 0) {
    current[pIdx] = progress;
  } else {
    current.push(progress);
  }
  state.judge.jobs[idx] = { ...job, progress: current };
}

function applyJudgeLogEvent(payload) {
  if (!payload || !payload.job_id) {
    return;
  }
  const idx = state.judge.jobs.findIndex((it) => it.id === payload.job_id);
  if (idx < 0) {
    return;
  }
  const job = state.judge.jobs[idx];
  const lines = Array.isArray(job.log_lines) ? [...job.log_lines] : [];
  if (payload.line !== undefined && payload.line !== null) {
    lines.push(String(payload.line));
  }
  const maxLines = 200;
  const nextLines = lines.length > maxLines ? lines.slice(-maxLines) : lines;
  state.judge.jobs[idx] = { ...job, log_lines: nextLines };
}

function stopJudgeStream() {
  if (state.judge.stream) {
    try {
      state.judge.stream.close();
    } catch (err) {
      console.error(err);
    }
    state.judge.stream = null;
  }
}

function startJudgeStream() {
  if (typeof EventSource === "undefined") {
    state.judge.streamFallback = true;
    startJudgePolling();
    return;
  }
  if (state.judge.stream) {
    return;
  }
  const since = Number(state.judge.streamSeq || 0);
  const url = `/api/judge/stream?since=${encodeURIComponent(since)}`;
  let stream;
  try {
    stream = new EventSource(url);
  } catch (err) {
    console.error(err);
    state.judge.streamFallback = true;
    startJudgePolling();
    return;
  }
  state.judge.stream = stream;
  state.judge.streamFallback = false;
  stream.onopen = () => {
    state.judge.streamFallback = false;
    stopJudgePolling();
  };

  stream.addEventListener("hello", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      state.judge.jobs = payload.jobs || [];
      state.judge.streamSeq = Number(payload.seq || state.judge.streamSeq || 0);
      updateJudgePageSections({ runs: false, batches: false, jobs: true, cli: false });
    } catch (err) {
      console.error(err);
    }
  });

  stream.addEventListener("job_upsert", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      if (payload && payload.job) {
        upsertJudgeJob(payload.job);
      }
      const seq = Number(event.lastEventId || 0);
      if (seq > 0) {
        state.judge.streamSeq = seq;
      }
      updateJudgePageSections({ runs: false, batches: false, jobs: true, cli: false });
    } catch (err) {
      console.error(err);
    }
  });

  stream.addEventListener("job_progress", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      applyJudgeProgressEvent(payload);
      const seq = Number(event.lastEventId || 0);
      if (seq > 0) {
        state.judge.streamSeq = seq;
      }
      updateJudgePageSections({ runs: false, batches: false, jobs: true, cli: false });
    } catch (err) {
      console.error(err);
    }
  });

  stream.addEventListener("job_log", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      applyJudgeLogEvent(payload);
      const seq = Number(event.lastEventId || 0);
      if (seq > 0) {
        state.judge.streamSeq = seq;
      }
      updateJudgePageSections({ runs: false, batches: false, jobs: true, cli: false });
    } catch (err) {
      console.error(err);
    }
  });

  stream.addEventListener("invalidate_runs_batches", async (event) => {
    try {
      const seq = Number(event.lastEventId || 0);
      if (seq > 0) {
        state.judge.streamSeq = seq;
      }
      await loadJudgeRunsAndBatches();
      updateJudgePageSections({ runs: true, batches: true, jobs: false, cli: false });
    } catch (err) {
      console.error(err);
    }
  });

  stream.addEventListener("heartbeat", (event) => {
    const seq = Number(event.lastEventId || 0);
    if (seq > 0) {
      state.judge.streamSeq = seq;
    }
  });

  stream.onerror = () => {
    stopJudgeStream();
    if (state.viewMode !== "judge") {
      return;
    }
    if (!state.judge.streamFallback) {
      state.judge.streamFallback = true;
      startJudgePolling();
    }
  };
}

async function openJudgeCliPanel(targetType, targetPath, dryRun) {
  state.judge.cliPanel = {
    target_type: targetType,
    target_path: targetPath,
    dry_run: !!dryRun,
    judge_env_file: "",
    preview: null,
  };
  await refreshJudgeCliPreview();
  updateJudgePageSections({ runs: false, batches: false, jobs: false, cli: true });
}

function closeJudgeCliPanel() {
  state.judge.cliPanel = null;
  updateJudgePageSections({ runs: false, batches: false, jobs: false, cli: true });
}

async function refreshJudgeCliPreview() {
  const panel = state.judge.cliPanel;
  if (!panel) {
    return;
  }
  try {
    const preview = await fetchJSON("/api/judge/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_type: panel.target_type,
        target_path: panel.target_path,
        dry_run: !!panel.dry_run,
        judge_env_file: panel.judge_env_file || "",
      }),
    });
    panel.preview = preview;
  } catch (err) {
    panel.preview = { ok: false, error: err.message || String(err) };
  }
}

async function startJudgeAction(targetType, targetPath, dryRun) {
  const ok = confirm(`Run judge for ${targetType} ${targetPath}? This overwrites judge artifacts.`);
  if (!ok) {
    return;
  }
  try {
    const resp = await fetchJSON("/api/judge/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_type: targetType,
        target_path: targetPath,
        dry_run: !!dryRun,
      }),
    });
    if (resp.error) {
      alert(resp.error);
      return;
    }
  } catch (err) {
    alert(err.message || String(err));
    return;
  }
  await loadJudgeData();
  updateJudgePageSections({ runs: true, batches: true, jobs: true, cli: false });
}

function attachJudgeHandlers() {
  if (state.judge.handlersAttached) {
    return;
  }
  appEl.addEventListener("click", async (event) => {
    if (state.viewMode !== "judge" || !(event.target instanceof Element)) {
      return;
    }
    const subviewBtn = event.target.closest("[data-judge-subview]");
    if (subviewBtn) {
      setJudgeSubview(subviewBtn.dataset.judgeSubview);
      return;
    }
    const startBtn = event.target.closest("[data-judge-action='start']");
    if (startBtn) {
      startJudgeAction(startBtn.dataset.targetType, startBtn.dataset.targetPath, startBtn.dataset.dryRun === "1");
      return;
    }
    const cliBtn = event.target.closest("[data-judge-action='cli']");
    if (cliBtn) {
      openJudgeCliPanel(cliBtn.dataset.targetType, cliBtn.dataset.targetPath, cliBtn.dataset.dryRun === "1");
      return;
    }
    if (event.target.closest("#judge-cli-close")) {
      closeJudgeCliPanel();
      return;
    }
    if (event.target.closest("#judge-cli-refresh")) {
      const panel = state.judge.cliPanel;
      if (!panel) {
        return;
      }
      const envEl = document.getElementById("judge-cli-env");
      const dryEl = document.getElementById("judge-cli-dry-run");
      panel.judge_env_file = envEl ? envEl.value : "";
      panel.dry_run = !!(dryEl && dryEl.checked);
      await refreshJudgeCliPreview();
      updateJudgePageSections({ runs: false, batches: false, jobs: false, cli: true });
      return;
    }
    if (event.target.closest("#judge-cli-copy")) {
      const text = state.judge.cliPanel?.preview?.command_one_line || "";
      if (!text) {
        return;
      }
      await copyText(text);
      alert("Copied judge command");
    }
  });
  state.judge.handlersAttached = true;
}

function startPolling() {
  if (state.pollTimer) {
    return;
  }
  state.pollTimer = setInterval(async () => {
    const status = await fetchJSON("/api/run/status");
    const prev = state.runStatus;
    state.runStatus = status;
    await refreshProxyStatus();
    if (state.viewMode !== "manual") {
      return;
    }
    if (status.status === "idle") {
      stopPolling();
      renderHome();
      return;
    }
    await maybeLoadMetrics(status);
    if (shouldRerender(prev, status)) {
      renderRun();
    } else {
      updateRunView(status, prev);
    }
  }, 2000);
}

async function refreshProxyStatus() {
  try {
    state.proxyStatus = await fetchJSON("/api/proxy/status");
  } catch (err) {
    state.proxyStatus = { status: "error", error: err.message || String(err) };
  }
  renderClusterStatus(state.clusterOk, state.clusterError, state.proxyStatus);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function setViewMode(mode) {
  state.viewMode = mode === "judge" ? "judge" : mode === "workflow" ? "workflow" : "manual";
  updateTopNav();
  if (state.viewMode === "judge") {
    stopPolling();
    stopWorkflowStream();
    stopWorkflowPolling();
    try {
      const savedSubview = window.localStorage.getItem("judge-subview");
      if (savedSubview === "batches" || savedSubview === "runs") {
        state.judge.subview = savedSubview;
      }
    } catch (err) {
      console.error(err);
    }
    try {
      await loadJudgeData();
    } catch (err) {
      alert(`Failed to load judge data: ${err.message || String(err)}`);
    }
    renderJudgePage();
    startJudgeStream();
    return;
  }
  if (state.viewMode === "workflow") {
    stopPolling();
    stopJudgeStream();
    stopJudgePolling();
    try {
      await Promise.all([loadWorkflowCatalog(), loadWorkflowFiles(), loadWorkflowJobs()]);
      await _workflowPrimeDraftCaseParams(state.workflow.builder.draft);
      await refreshWorkflowCliPreview();
    } catch (err) {
      alert(`Failed to load workflow data: ${err.message || String(err)}`);
    }
    renderWorkflowPage();
    startWorkflowStream();
    return;
  }
  stopJudgeStream();
  stopJudgePolling();
  stopWorkflowStream();
  stopWorkflowPolling();
  if (state.runStatus && state.runStatus.status && state.runStatus.status !== "idle") {
    renderRun();
    startPolling();
    return;
  }
  rerenderCurrentView();
}

async function loadServices() {
  const data = await fetchJSON("/api/services");
  state.services = data.services || [];
  state.clusterOk = data.cluster_ok;
  state.clusterError = data.cluster_error;
  try {
    state.proxyStatus = await fetchJSON("/api/proxy/status");
  } catch (err) {
    state.proxyStatus = { status: "error", error: err.message || String(err) };
  }

  try {
    state.orchestratorOptions = await fetchJSON("/api/orchestrator/options");
  } catch (err) {
    state.orchestratorOptions = getFallbackOrchestratorOptions();
  }
  initializeCommandFlags(true);

  renderClusterStatus(state.clusterOk, state.clusterError, state.proxyStatus);
}

async function loadService(serviceName) {
  state.currentService = serviceName;
  state.commandBuilder.visible = false;
  const data = await fetchJSON(`/api/services/${serviceName}/cases`);
  state.currentCases = data.cases || [];
  renderCases();
}

async function loadCase(caseId) {
  state.currentCase = caseId;
  state.commandBuilder.visible = false;
  const data = await fetchJSON(`/api/cases/${caseId}`);
  if (data.error) {
    alert(data.error);
    return;
  }
  state.currentCaseDetails = data;
  renderCaseDetail();
}

async function startRun(caseId) {
  const ok = confirm("Start the pre-operation setup for this test?");
  if (!ok) {
    return;
  }
  state.runMetrics = null;
  state.lastMetricsPath = null;
  const result = await fetchJSON("/api/run/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ case_id: caseId }),
  });
  if (result.error) {
    alert(result.error);
    return;
  }
  state.setupAlerted = false;
  const status = await fetchJSON("/api/run/status");
  state.runStatus = status;
  await maybeLoadMetrics(status);
  renderRun();
  startPolling();
}

async function backToCases() {
  try {
    const result = await fetchJSON("/api/run/cleanup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (result.error) {
      alert(result.error);
      return;
    }
  } catch (err) {
    alert(`Cleanup failed: ${err.message || String(err)}`);
  }

  stopPolling();
  const service =
    state.currentCaseDetails?.service ||
    state.runStatus?.case?.service ||
    state.currentService;
  if (service) {
    await loadService(service);
  } else {
    renderHome();
  }
}

async function submitRun() {
  const result = await fetchJSON("/api/run/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (result.warning) {
    alert(result.warning);
    return;
  }
  if (result.error) {
    alert(result.error);
    return;
  }
  const status = await fetchJSON("/api/run/status");
  state.runStatus = status;
  await maybeLoadMetrics(status);
  renderRun();
}

async function init() {
  navManualEl.addEventListener("click", () => setViewMode("manual"));
  if (navWorkflowEl) {
    navWorkflowEl.addEventListener("click", () => setViewMode("workflow"));
  }
  navJudgeEl.addEventListener("click", () => setViewMode("judge"));
  updateTopNav();

  try {
    await loadServices();
  } catch (err) {
    appEl.innerHTML = `<section class="section"><div class="pre">Failed to load services: ${escapeHtml(
      err.message || String(err)
    )}</div></section>`;
    return;
  }

  try {
    const status = await fetchJSON("/api/run/status");
    state.runStatus = status;
    if (status.status && status.status !== "idle") {
      if (status.case && status.case.id) {
        const caseData = await fetchJSON(`/api/cases/${status.case.id}`);
        if (!caseData.error) {
          state.currentCaseDetails = caseData;
        }
      }
      await maybeLoadMetrics(status);
      renderRun();
      startPolling();
      return;
    }
  } catch (err) {
    console.error(err);
  }

  renderHome();
}

init();

async function maybeLoadMetrics(status) {
  if (!status || !status.metrics_path) {
    return;
  }
  if (status.status === "ready" || status.status === "failed" || status.status === "verifying") {
    return;
  }
  if (state.lastMetricsPath === status.metrics_path && state.runMetrics) {
    return;
  }
  try {
    const data = await fetchJSON("/api/run/metrics");
    state.runMetrics = data;
    state.lastMetricsPath = status.metrics_path;
  } catch (err) {
    state.runMetrics = { error: err.message || String(err) };
  }
}

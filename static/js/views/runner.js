/*
 * KARMA web UI -- Runner view.
 *
 * Browse services and cases, inspect a case's prompt and parameters, then
 * run it one of two ways:
 *   - Agent run: POST /api/run and stream stage events live.
 *   - Manual run: POST /api/manual/start, poll setup phase to ready, let the
 *     operator do the task, then submit for verification and clean up.
 * A collapsible command builder renders the equivalent CLI via
 * /api/cli/preview.
 */
(function () {
  "use strict";
  const KARMA = window.KARMA;
  const { el, clear, api, escape } = KARMA;

  let root;
  let agents = [];

  function errBox(e) {
    const m = e.message || String(e);
    KARMA.toast(m, "error");
    return el("div", { class: "error-box" }, m);
  }
  function backBtn(fn) {
    return el("button", { class: "btn secondary", onClick: fn }, "← Back");
  }

  async function mount(container) {
    root = container;
    if (!agents.length) {
      try { agents = await api.get("/api/agents"); } catch (_e) { agents = []; }
    }
    renderHome();
  }

  function serviceCard(svc) {
    const desc = KARMA.labels.serviceDescription(svc.name);
    const names = (svc.cases || []).map((c) => KARMA.labels.case(c));
    const shown = names.slice(0, 6).join(", ");
    const more = names.length > 6 ? ` +${names.length - 6} more` : "";
    return el("div", { class: "card service-card", onClick: () => renderService(svc.name) },
      el("div", { class: "title" }, KARMA.labels.service(svc.name)),
      desc ? el("div", { class: "service-desc" }, desc) : null,
      el("div", { class: "service-cases" },
        el("span", { class: "count" }, `${svc.case_count} case${svc.case_count === 1 ? "" : "s"}`),
        shown ? "  ·  " + shown + more : ""));
  }

  async function renderHome() {
    clear(root);
    root.appendChild(el("h2", {}, "Run a Case"));
    try {
      const data = await api.get("/api/services");
      if (!data.services.length) {
        root.appendChild(el("p", { class: "muted" }, "No services found under resources/."));
        return;
      }
      const apps = data.services.filter((s) => !KARMA.labels.isExampleService(s.name));
      const examples = data.services.filter((s) => KARMA.labels.isExampleService(s.name));

      if (apps.length) {
        root.appendChild(el("h3", {}, "Applications"));
        const grid = el("div", { class: "service-grid" });
        apps.forEach((s) => grid.appendChild(serviceCard(s)));
        root.appendChild(grid);
      }
      if (examples.length) {
        root.appendChild(el("h3", {}, "Examples"));
        const grid = el("div", { class: "service-grid" });
        examples.forEach((s) => grid.appendChild(serviceCard(s)));
        root.appendChild(grid);
      }
    } catch (e) { root.appendChild(errBox(e)); }
  }

  async function renderService(service) {
    clear(root);
    root.appendChild(el("div", { class: "page-head" },
      backBtn(renderHome), el("h2", {}, KARMA.labels.service(service))));
    const desc = KARMA.labels.serviceDescription(service);
    if (desc) root.appendChild(el("p", { class: "field-help" }, desc));
    const grid = el("div", { class: "service-grid" });
    root.appendChild(grid);
    try {
      const data = await api.get("/api/services");
      const svc = data.services.find((s) => s.name === service);
      for (const c of (svc ? svc.cases : [])) {
        const card = caseCard(service, c);
        grid.appendChild(card.node);
        card.load();
      }
    } catch (e) { root.appendChild(errBox(e)); }
  }

  // A case card showing the test name, a prompt excerpt, and quick facts.
  function caseCard(service, caseName) {
    const sub = el("div", { class: "service-desc" }, "Loading…");
    const facts = el("div", { class: "service-cases" });
    const node = el("div", { class: "card service-card", onClick: () => renderCase(service, caseName) },
      el("div", { class: "title" }, KARMA.labels.case(caseName)), sub, facts);
    async function load() {
      try {
        const d = await api.get(`/api/cases/${service}/${caseName}`);
        const prompt = (d.prompt || "").trim().replace(/\s+/g, " ");
        sub.textContent = prompt ? (prompt.length > 150 ? prompt.slice(0, 150) + "…" : prompt) : "—";
        clear(facts);
        facts.appendChild(el("span", { class: "count" }, `${d.precondition_count} preconditions`));
        facts.appendChild(document.createTextNode(`  ·  ${d.metrics.length} metrics`));
        if (d.params && d.params.length) {
          facts.appendChild(document.createTextNode(`  ·  ${d.params.length} params`));
        }
      } catch (_e) { sub.textContent = ""; }
    }
    return { node, load };
  }

  async function renderCase(service, caseName) {
    clear(root);
    let detail;
    try {
      detail = await api.get(`/api/cases/${service}/${caseName}`);
    } catch (e) {
      root.appendChild(el("div", { class: "page-head" }, backBtn(() => renderService(service))));
      root.appendChild(errBox(e));
      return;
    }

    root.appendChild(el("div", { class: "page-head" },
      backBtn(() => renderService(service)),
      el("h2", {}, `${KARMA.labels.service(service)} · ${KARMA.labels.case(caseName)}`)));

    // Metadata badges
    const badges = el("div", { class: "toolbar" });
    badges.appendChild(el("span", { class: "badge" }, detail.precondition_count + " preconditions"));
    badges.appendChild(el("span", { class: "badge" }, detail.metrics.length + " metrics"));
    for (const t of detail.tags) badges.appendChild(el("span", { class: "badge" }, KARMA.humanize(t)));
    root.appendChild(badges);

    // Prompt
    const promptPanel = el("div", { class: "panel" });
    promptPanel.appendChild(el("h3", {}, "Prompt"));
    promptPanel.appendChild(el("pre", { class: "log" }, detail.prompt || "(none)"));
    root.appendChild(promptPanel);

    // Params form
    const cfg = el("div", { class: "panel" });
    cfg.appendChild(el("h3", {}, "Parameters & Run Config"));
    cfg.appendChild(el("p", { class: "field-help" },
      "Adjust the case parameters if needed, then choose how to run it: which agent " +
      "(or none to run locally without one), local or Docker sandbox, and a timeout."));
    const paramInputs = {};
    for (const p of detail.params) {
      cfg.appendChild(el("label", {}, `${KARMA.labels.case(p.name)}${p.description ? " — " + p.description : ""}`));
      const input = el("input", { value: p.default == null ? "" : String(p.default) });
      paramInputs[p.name] = input;
      cfg.appendChild(input);
    }

    const row = el("div", { class: "row" });
    const agentSel = el("select", {},
      el("option", { value: "" }, "None — run locally"),
      ...agents.map((a) => el("option", { value: a }, KARMA.labels.agent(a))));
    const sandboxSel = el("select", {},
      el("option", { value: "local" }, "Local"),
      el("option", { value: "docker" }, "Docker"));
    const timeoutInput = el("input", { type: "number", value: "900" });
    row.appendChild(el("div", {}, el("label", {}, "Agent"), agentSel));
    row.appendChild(el("div", {}, el("label", {}, "Sandbox"), sandboxSel));
    row.appendChild(el("div", {}, el("label", {}, "Timeout (s)"), timeoutInput));
    cfg.appendChild(row);

    function collectParams() {
      const out = {};
      for (const [k, input] of Object.entries(paramInputs)) {
        if (input.value !== "") out[k] = input.value;
      }
      return out;
    }

    const status = el("div", { class: "panel", id: "run-status" });
    status.appendChild(el("p", { class: "muted" }, "Run output appears here."));

    const actions = el("div", { class: "toolbar" });
    actions.appendChild(el("button", { class: "btn", onClick: () =>
      startAgentRun(service, caseName, collectParams(), agentSel.value, sandboxSel.value,
        Number(timeoutInput.value) || 900, status) },
      "Run with agent"));
    actions.appendChild(el("button", { class: "btn secondary", onClick: () =>
      startManualRun(service, caseName, collectParams(), status) },
      "Start manual run"));
    cfg.appendChild(actions);
    root.appendChild(cfg);
    root.appendChild(status);

    // CLI command — auto-generated, updates as the run config changes.
    const cli = buildCliPanel(service, caseName, agentSel, sandboxSel, timeoutInput, collectParams);
    root.appendChild(cli.node);
    [agentSel, sandboxSel].forEach((elm) => elm.addEventListener("change", cli.refresh));
    timeoutInput.addEventListener("input", cli.refresh);
    Object.values(paramInputs).forEach((inp) => inp.addEventListener("input", cli.refresh));
    cli.refresh();
  }

  // --- Agent run ------------------------------------------------------------
  async function startAgentRun(service, caseName, params, agent, sandbox, timeout, status) {
    clear(status);
    status.appendChild(el("h3", {}, "Agent Run"));
    const log = el("pre", { class: "log" }, "Submitting…\n");
    status.appendChild(log);
    try {
      const { run_id } = await api.post("/api/run", {
        service, case_name: caseName, params, agent: agent || null, sandbox,
        agent_timeout_sec: timeout,
      });
      log.textContent += `run_id: ${run_id}\n`;
      const cancelBtn = el("button", { class: "btn secondary", onClick: () => {
        api.post(`/api/run/${run_id}/cancel`).catch(() => {});
      } }, "Cancel");
      status.insertBefore(cancelBtn, log);
      api.stream(`/api/run/${run_id}/stream`, {
        statusPath: `/api/run/${run_id}/status`,
        onEvent: (ev) => {
          if (ev.type === "stage_complete") {
            const s = ev.stage || {};
            log.textContent += `stage ${s.stage_id}: ${s.status} (oracle=${s.oracle_verdict})\n`;
          } else if (ev.type === "run_complete") {
            log.textContent += `run complete: ${ev.status}\n`;
            KARMA.toast("Run " + KARMA.labels.status(ev.status).text.toLowerCase(),
              ev.status === "complete" ? "success" : "error");
          } else if (ev.type === "cancelled") {
            log.textContent += "cancelled\n";
          }
          log.scrollTop = log.scrollHeight;
        },
        onDone: () => { log.textContent += "— stream ended —\n"; },
      });
    } catch (e) {
      log.textContent += "Error: " + e.message + "\n";
      KARMA.toastError(e);
    }
  }

  // --- Manual run -----------------------------------------------------------
  async function startManualRun(service, caseName, params, status) {
    clear(status);
    status.appendChild(el("h3", {}, "Manual Run"));
    const phase = el("p", { class: "muted" }, "Starting setup…");
    status.appendChild(phase);
    const detailBox = el("div", {});
    status.appendChild(detailBox);

    let runId;
    try {
      const resp = await api.post("/api/manual/start", { service, case_name: caseName, params });
      runId = resp.run_id;
    } catch (e) { phase.textContent = "Error: " + e.message; KARMA.toastError(e); return; }

    async function poll() {
      let st;
      try { st = await api.get(`/api/manual/${runId}/status`); }
      catch (e) { phase.textContent = "Error: " + e.message; KARMA.toastError(e); return; }
      phase.textContent = `Status: ${KARMA.labels.status(st.status).text}`
        + (st.phase ? ` (${KARMA.humanize(st.phase)})` : "");
      if (st.status === "setup_running") { setTimeout(poll, 1500); return; }
      if (st.status === "setup_failed") {
        phase.className = "badge bad";
        detailBox.appendChild(el("p", {}, st.error || "setup failed"));
        return;
      }
      renderManualReady(runId, st, detailBox, phase);
    }
    poll();
  }

  function renderManualReady(runId, st, box, phase) {
    clear(box);
    if (st.status === "ready" || st.status === "failed" || st.status === "passed") {
      const ns = st.namespace_bindings || {};
      box.appendChild(el("p", {}, "Namespaces: " +
        Object.entries(ns).map(([r, n]) => `${r}=${n}`).join(", ")));
      if (st.kubeconfig_path) {
        box.appendChild(el("p", { class: "muted" }, "KUBECONFIG: " + st.kubeconfig_path));
      }
      const bar = el("div", { class: "toolbar" });
      bar.appendChild(el("button", { class: "btn", onClick: async () => {
        phase.textContent = "Verifying…";
        try {
          const r = await api.post(`/api/manual/${runId}/submit`);
          phase.textContent = "Result: " + KARMA.labels.status(r.status).text + ` (attempt ${r.attempts})`;
          phase.className = r.status === "passed" ? "badge ok" : "badge bad";
          KARMA.toast("Manual run " + KARMA.labels.status(r.status).text.toLowerCase(),
            r.status === "passed" ? "success" : "error");
        } catch (e) { phase.textContent = "Error: " + e.message; KARMA.toastError(e); }
      } }, "Submit for verification"));
      bar.appendChild(el("button", { class: "btn secondary", onClick: async () => {
        await api.post(`/api/manual/${runId}/cleanup`).catch(() => {});
        phase.textContent = "Cleaned up.";
        clear(box);
      } }, "Cleanup"));
      box.appendChild(bar);
      box.appendChild(el("p", { class: "muted" },
        "Do the task by hand against the namespace above, then Submit."));
    }
  }

  // --- CLI command ----------------------------------------------------------
  // The equivalent terminal command for the current selections. Auto-updates
  // as the run config changes; shown in a code block with an in-block Copy.
  function buildCliPanel(service, caseName, agentSel, sandboxSel, timeoutInput, collectParams) {
    const code = el("pre", { class: "log" }, "");
    const copy = el("button", { class: "code-copy", title: "Copy command", onClick: () => {
      if (navigator.clipboard) navigator.clipboard.writeText(code.textContent);
      copy.textContent = "Copied";
      setTimeout(() => { copy.textContent = "Copy"; }, 1200);
    } }, "Copy");
    const note = el("div", { class: "field-help", style: "margin-top:10px" });

    const panel = el("div", { class: "panel" },
      el("h3", {}, "CLI Command"),
      el("p", { class: "field-help" }, "Prefer the terminal? Copy and run this to launch the same case:"),
      el("div", { class: "code-block" }, copy, code),
      note);

    async function refresh() {
      try {
        const res = await api.post("/api/cli/preview", {
          command: "case",
          target: { service, case: caseName },
          flags: {
            agent: agentSel.value, sandbox: sandboxSel.value,
            timeout: Number(timeoutInput.value) || 900, params: collectParams(),
          },
        });
        code.textContent = res.command_multi_line || res.command_one_line || "";
        // Errors (red) mean the command won't work; warnings are gentle notes.
        const errs = res.errors || [], warns = res.warnings || [];
        if (errs.length) { note.style.color = "var(--bad)"; note.textContent = errs.join(" · "); }
        else { note.style.color = ""; note.textContent = warns.join(" · "); }
      } catch (_e) { /* keep last good command */ }
    }
    return { node: panel, refresh };
  }

  KARMA.registerView({ id: "runner", label: "Run", mount });
})();

/*
 * KARMA web UI -- Adversary view.
 *
 * Lists the adversary scenarios discovered under resources/ and lets an
 * operator inject one into a live manual run by hand, then lift it. The old
 * UI had no such control; the engine always supported it, so this surfaces
 * it. Deploy/lift target a manual run by id (start one in the Runner view,
 * paste its id here), calling /api/manual/<id>/adversary/{deploy,lift}.
 */
(function () {
  "use strict";
  const KARMA = window.KARMA;
  const { el, clear, api } = KARMA;

  let root;
  let runIdInput;

  function errBox(e) { return el("div", { class: "error-box" }, e.message || String(e)); }

  function mount(container) { root = container; render(); }

  async function render() {
    clear(root);
    root.appendChild(el("h2", {}, "Adversary injection"));

    const ctl = el("div", { class: "panel" });
    ctl.appendChild(el("p", { class: "muted" },
      "Start a manual run in the Runner tab, then paste its run id here to " +
      "inject or lift a scenario against that run's namespaces."));
    runIdInput = el("input", { placeholder: "manual run id" });
    ctl.appendChild(el("label", {}, "Manual run id"));
    ctl.appendChild(runIdInput);
    root.appendChild(ctl);

    const log = el("pre", { class: "log", id: "adv-log" }, "Adversary output appears here.\n");

    const grid = el("div", { class: "grid" });
    root.appendChild(grid);
    root.appendChild(log);

    try {
      const scenarios = await api.get("/api/adversary/scenarios");
      if (!scenarios.length) {
        grid.appendChild(el("p", { class: "muted" }, "No adversary scenarios found under resources/*/adversarial/."));
      }
      for (const s of scenarios) {
        const card = el("div", { class: "card", style: "cursor:default" });
        card.appendChild(el("div", { class: "title" }, s.scenario));
        card.appendChild(el("div", { class: "sub" }, s.service + (s.has_lift ? " · liftable" : " · no lift")));
        const hint = (s.prompt_hints && s.prompt_hints.deploy) || "";
        if (hint) card.appendChild(el("div", { class: "sub" }, hint));
        const bar = el("div", { class: "toolbar", style: "margin-top:8px" });
        bar.appendChild(el("button", { class: "btn", onClick: () => deploy(s.scenario) }, "Deploy"));
        bar.appendChild(el("button", {
          class: "btn secondary", disabled: !s.has_lift ? "disabled" : null,
          onClick: () => lift(s.scenario),
        }, "Lift"));
        card.appendChild(bar);
        grid.appendChild(card);
      }
    } catch (e) { grid.appendChild(errBox(e)); }
  }

  function runId() { return (runIdInput.value || "").trim(); }

  async function deploy(scenario) {
    const log = document.getElementById("adv-log");
    const id = runId();
    if (!id) { log.textContent = "Enter a manual run id first.\n"; return; }
    log.textContent += `deploying ${scenario} into ${id}…\n`;
    try {
      const r = await api.post(`/api/manual/${id}/adversary/deploy`, { scenario });
      log.textContent += `  deploy ok=${r.deploy && r.deploy.ok}\n`;
    } catch (e) { log.textContent += "Error: " + e.message + "\n"; }
    log.scrollTop = log.scrollHeight;
  }

  async function lift(scenario) {
    const log = document.getElementById("adv-log");
    const id = runId();
    if (!id) { log.textContent = "Enter a manual run id first.\n"; return; }
    log.textContent += `lifting ${scenario} from ${id}…\n`;
    try {
      const r = await api.post(`/api/manual/${id}/adversary/lift`, { scenario });
      log.textContent += `  lift ok=${r.lift && r.lift.ok}\n`;
    } catch (e) { log.textContent += "Error: " + e.message + "\n"; }
    log.scrollTop = log.scrollHeight;
  }

  KARMA.registerView({ id: "adversary", label: "Adversary", mount });
})();

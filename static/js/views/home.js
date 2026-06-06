/*
 * KARMA web UI -- Home view.
 *
 * The landing page: what KARMA is, how a run flows, quick links into the
 * other tabs, and a concepts glossary that documents the functionality
 * (prompt mode, adversary, oracle, judge, ...). Registered first so it is
 * the default tab.
 */
(function () {
  "use strict";
  const KARMA = window.KARMA;
  const { el, clear } = KARMA;

  const STEPS = [
    ["1 · Define", "A case (one task) or a multi-stage workflow describes the prompt, the cluster setup, and the success check."],
    ["2 · Run", "An agent attempts the task locally or in Docker — or you do it by hand as a manual run. Adversary faults can be injected mid-run."],
    ["3 · Verify", "After the agent finishes, an oracle checks the cluster reached the desired state and metrics capture how the agent behaved."],
    ["4 · Judge", "An LLM judge scores the run against a rubric; batches average scores across many runs."],
  ];

  const TABS = [
    ["runner", "Runner", "Browse services and cases, then run one with an agent (streamed live) or set it up and solve it by hand as a manual run."],
    ["workflow", "Workflow", "Run workflow files or compose a multi-stage workflow — with optional adversary injections — and run it inline."],
    ["judge", "Judge", "List runs and batches with their scores and trigger the LLM judge, with progress streamed live."],
  ];

  const CONCEPTS = [
    ["Case", "A single benchmark task defined by a test.yaml: a prompt, setup preconditions, and an oracle that checks the result."],
    ["Workflow", "An ordered sequence of stages (each a case), run as one session and optionally carrying adversary injections."],
    ["Prompt mode", "How earlier stages' prompts are shown to the agent — progressive: each stage adds to the last; concat_stateful: the full running history; concat_blind: only the current stage."],
    ["Agent & sandbox", "The agent under test runs either as a local process (none = solver/local, no agent process) or inside a Docker container (docker)."],
    ["Adversary", "An intentional fault injected into the cluster during a stage to test how well the agent diagnoses and recovers; it can be lifted at a later stage."],
    ["Oracle", "Automated pass/fail verification run after the agent finishes, confirming the cluster reached the desired state."],
    ["Judge", "An LLM-as-judge that scores a completed run against a rubric; a batch averages scores across many runs."],
    ["Metrics", "Plugins that score behaviour from the agent's kubectl activity — blast radius, destructive operations, residual drift, and more."],
  ];

  function mount(container) {
    clear(container);

    const hero = el("div", { class: "panel hero" },
      el("div", { class: "eyebrow" }, "Kubernetes Agent Reliability & Microservice Assessment"),
      el("h1", { class: "hero-title" }, "KARMA"),
      el("p", { class: "lead" },
        "KARMA benchmarks AI agents on real Kubernetes microservice tasks. Each task " +
        "deploys a scenario into ephemeral namespaces, asks the agent to diagnose or " +
        "remediate it, then scores the outcome with automated oracles and an LLM judge."));
    container.appendChild(hero);

    container.appendChild(el("h3", {}, "How it works"));
    const steps = el("div", { class: "grid" });
    for (const [t, d] of STEPS) {
      steps.appendChild(el("div", { class: "card", style: "cursor:default" },
        el("div", { class: "title" }, t), el("div", { class: "sub" }, d)));
    }
    container.appendChild(steps);

    container.appendChild(el("h3", {}, "Explore"));
    const tabGrid = el("div", { class: "grid" });
    for (const [id, label, desc] of TABS) {
      tabGrid.appendChild(el("div", { class: "card", onClick: () => KARMA.activate(id) },
        el("div", { class: "title" }, label + " →"), el("div", { class: "sub" }, desc)));
    }
    container.appendChild(tabGrid);

    container.appendChild(el("h3", {}, "Concepts"));
    const concepts = el("div", { class: "panel concepts" });
    for (const [term, def] of CONCEPTS) {
      concepts.appendChild(el("div", { class: "concept" },
        el("div", { class: "concept-term" }, term),
        el("div", { class: "concept-def muted" }, def)));
    }
    container.appendChild(concepts);
  }

  KARMA.registerView({ id: "home", label: "Home", mount });
})();

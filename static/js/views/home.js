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
    ["Define", "A case or multi-stage workflow specifies the prompt, the cluster setup, and the success check."],
    ["Run", "An agent attempts the task locally or in Docker, or you perform it by hand as a manual run."],
    ["Verify", "An oracle checks the resulting cluster state, and metrics capture how the agent behaved."],
    ["Judge", "An LLM judge scores the run against a rubric; batches aggregate scores across runs."],
  ];

  const TABS = [
    ["runner", "Run", "Browse services and cases, then run one with an agent (streamed live) or set it up and solve it by hand as a manual run."],
    ["workflow", "Workflow", "Run workflow files or compose a multi-stage workflow — with optional adversary injections — and run it inline."],
    ["judge", "Judge", "List runs and batches with their scores and trigger the LLM judge, with progress streamed live."],
  ];

  const CONCEPTS = [
    ["Case", "A single benchmark task, defined by a test.yaml file. It specifies the agent's prompt, the preconditions used to prepare the cluster, and the oracle that determines success."],
    ["Workflow", "An ordered sequence of stages — each stage is one case — executed as a single session. A workflow may also declare adversarial scenario injections."],
    ["Prompt mode", "Determines how the prompts from earlier stages are presented to the agent. Progressive appends each stage to the previous one; Concatenated (stateful) provides the full running history; Concatenated (blind) provides only the current stage."],
    ["Agent & sandbox", "The agent is the system under test. It runs either as a local process (or none, to run without an agent) or inside a Docker container, as selected by the sandbox setting."],
    ["Adversary", "A deliberate fault injected into the cluster during a stage to evaluate how well the agent detects and recovers from it. It can optionally be lifted at a later stage."],
    ["Oracle", "An automated check that runs after the agent finishes and returns a pass or fail verdict by confirming the cluster reached its intended state."],
    ["Judge", "An LLM-based evaluator that scores a completed run against a rubric. A batch aggregates the average score across many runs."],
    ["Metrics", "Plugins that quantify the agent's behaviour from its observed kubectl activity — for example blast radius, destructive operations, and residual drift."],
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

    container.appendChild(el("h3", { class: "home-section" }, "How It Works"));
    const flow = el("div", { class: "flow" });
    STEPS.forEach(([t, d], i) => {
      if (i > 0) flow.appendChild(el("div", { class: "flow-arrow" }, "→"));
      flow.appendChild(el("div", { class: "flow-step" },
        el("div", { class: "flow-num" }, String(i + 1)),
        el("div", { class: "flow-step-title" }, t),
        el("div", { class: "flow-step-desc" }, d)));
    });
    container.appendChild(flow);

    container.appendChild(el("h3", { class: "home-section" }, "Explore"));
    const tabGrid = el("div", { class: "grid explore-grid" });
    for (const [id, label, desc] of TABS) {
      tabGrid.appendChild(el("div", { class: "card", onClick: () => KARMA.activate(id) },
        el("div", { class: "title" }, label + " →"), el("div", { class: "sub" }, desc)));
    }
    container.appendChild(tabGrid);

    container.appendChild(el("h3", { class: "home-section" }, "Concepts"));
    const concepts = el("div", { class: "panel concepts" });
    for (const [term, def] of CONCEPTS) {
      concepts.appendChild(el("div", { class: "concept" },
        el("div", { class: "concept-term" }, term),
        el("div", { class: "concept-def muted" }, def)));
    }
    container.appendChild(concepts);
  }

  // hidden: the brand in the top bar is the way back here, so it is not a tab.
  KARMA.registerView({ id: "home", label: "Home", mount, hidden: true });
})();

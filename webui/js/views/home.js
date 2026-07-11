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
    ["Define", "Each task is a case (one test.yaml) or a workflow of several cases. It declares the prompt the agent is given, the preconditions that build the starting cluster state, and the oracle that defines success."],
    ["Set up", "KARMA creates fresh, ephemeral namespaces and runs the preconditions to deploy the scenario — optionally injecting an adversarial fault. The agent receives a scoped kubeconfig and the rendered prompt."],
    ["Run", "The agent works the task — as a local process or inside a Docker container — issuing kubectl/API calls until it signals completion or hits the time limit. Or a person does it by hand as a manual run."],
    ["Verify", "The agent is stopped and its evidence (logs and kubectl activity) is collected. The oracle checks whether the cluster reached its intended state and returns pass or fail; metric plugins quantify behaviour such as blast radius and residual drift."],
    ["Judge", "An LLM judge scores the run 0–100 against a rubric, reading the prompt, the agent log, and the oracle/regression results. A batch averages scores across many runs."],
  ];

  // [view id, label shown, description]. Ids/labels match the actual tabs.
  const TABS = [
    ["runner", "Cases", "Browse services and cases, then run one with an agent (streamed live) or set it up and solve it by hand as a manual run."],
    ["workflow", "Workflow", "Compose a multi-stage workflow — with optional adversary injections — and run it, or run a saved workflow file."],
    ["results", "Results", "Every run, live and historical: per-stage status, failure logs, the LLM judge score, and cross-run judge batches."],
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
      el("div", { class: "eyebrow" }, "Kubernetes Agent Runtime Measurement Architecture"),
      el("h1", { class: "hero-title" }, "KARMA"),
      el("p", { class: "lead" },
        "KARMA benchmarks AI agents on real Kubernetes microservice tasks. Each task " +
        "deploys a scenario into ephemeral namespaces, asks the agent to diagnose or " +
        "remediate it, then scores the outcome with automated oracles and an LLM judge."));
    container.appendChild(hero);

    container.appendChild(el("h3", { class: "home-section" }, "How It Works"));
    const flow = el("div", { class: "panel flow-timeline" });
    STEPS.forEach(([t, d], i) => {
      flow.appendChild(el("div", { class: "flow-step" },
        el("div", { class: "flow-num" }, String(i + 1)),
        el("div", { class: "flow-body" },
          el("div", { class: "flow-step-title" }, t),
          el("div", { class: "flow-step-desc" }, d))));
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

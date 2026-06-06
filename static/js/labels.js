/*
 * KARMA web UI -- display labels.
 *
 * The backend identifies services, cases, agents, metrics, statuses, etc. by
 * their raw directory / code names (cockroachdb, renew_tls_secret,
 * cli_runner, setup_running). Those are not friendly to read, so this module
 * maps an id to a human label for display ONLY -- callers keep sending the
 * raw id to the API (option values and request bodies are unchanged).
 *
 * humanize() splits on - and _ and Title-Cases each token, with a curated
 * acronym/word map so domain terms render correctly (TLS, HA, OTel,
 * ConfigMap). Unknown ids degrade gracefully, so a new service like "redis"
 * shows as "Redis" without a code change.
 */
(function () {
  "use strict";
  const KARMA = (window.KARMA = window.KARMA || {});

  // Tokens that should not be naively Title-Cased.
  const WORDS = {
    tls: "TLS", ssl: "SSL", ca: "CA", ha: "HA", http: "HTTP", https: "HTTPS",
    db: "DB", ns: "NS", oom: "OOM", etl: "ETL", pi: "PI", otel: "OTel",
    configmap: "ConfigMap", cli: "CLI", ui: "UI", api: "API", id: "ID",
    url: "URL", rbac: "RBAC", crd: "CRD", ip: "IP", tcp: "TCP", udp: "UDP",
    json: "JSON", yaml: "YAML", oidc: "OIDC", tpc: "TPC", sla: "SLA",
  };

  function humanize(id) {
    if (id == null) return "";
    return String(id)
      .split(/[-_\s]+/)
      .filter(Boolean)
      .map((tok) => {
        const lower = tok.toLowerCase();
        if (WORDS[lower]) return WORDS[lower];
        return tok.charAt(0).toUpperCase() + tok.slice(1);
      })
      .join(" ");
  }

  // Curated proper-noun names for services (fallback: humanize).
  const SERVICES = {
    cockroachdb: "CockroachDB",
    mongodb: "MongoDB",
    elasticsearch: "Elasticsearch",
    "nginx-ingress": "NGINX Ingress",
    "rabbitmq-experiments": "RabbitMQ",
    ray: "Ray",
    spark: "Spark",
    demo: "Demo",
  };

  // Services that are examples, not benchmarked applications.
  const EXAMPLE_SERVICES = new Set(["demo"]);

  // One-line intro per service (fallback: empty).
  const SERVICE_DESC = {
    cockroachdb: "Distributed SQL database — deployment, certificate rotation, scaling, upgrades, and recovery.",
    mongodb: "Document database replica sets — deployment, TLS, users and roles, scaling, and upgrades.",
    elasticsearch: "Search & analytics cluster — node scaling, certificate rotation, snapshots, and upgrades.",
    "nginx-ingress": "Ingress controller — TLS secrets, rate limiting, canary routing, and class upgrades.",
    "rabbitmq-experiments": "Message broker — queues, policies, TLS rotation, blue/green migration, and failover.",
    ray: "Distributed compute — cluster deploy/teardown, worker scaling, job submission, and recovery.",
    spark: "Big-data processing — job tuning, autoscaling, multi-tenancy, and ETL/OOM scenarios.",
    demo: "Tiny example tasks for trying KARMA end to end.",
  };

  const AGENTS = { cli_runner: "CLI Runner", react: "ReAct" };

  const PROMPT_MODES = {
    progressive: "Progressive",
    concat_stateful: "Concatenated (stateful)",
    concat_blind: "Concatenated (blind)",
  };

  // status -> {text, cls} where cls matches a .badge modifier.
  const STATUS = {
    setup_running: { text: "Setting up", cls: "run" },
    setup_failed: { text: "Setup failed", cls: "bad" },
    running: { text: "Running", cls: "run" },
    ready: { text: "Ready", cls: "ok" },
    verifying: { text: "Verifying", cls: "run" },
    passed: { text: "Passed", cls: "ok" },
    complete: { text: "Complete", cls: "ok" },
    failed: { text: "Failed", cls: "bad" },
    error: { text: "Error", cls: "bad" },
    cancelled: { text: "Cancelled", cls: "warn" },
    cleaned: { text: "Cleaned up", cls: "" },
    pending: { text: "Not judged", cls: "" },
    judged: { text: "Judged", cls: "ok" },
    unknown: { text: "Unknown", cls: "" },
  };

  KARMA.humanize = humanize;
  KARMA.labels = {
    service: (id) => SERVICES[id] || humanize(id),
    serviceDescription: (id) => SERVICE_DESC[id] || "",
    isExampleService: (id) => EXAMPLE_SERVICES.has(id),
    case: (id) => humanize(id),
    scenario: (id) => humanize(id),
    agent: (id) => AGENTS[id] || humanize(id),
    metric: (id) => humanize(id),
    promptMode: (id) => PROMPT_MODES[id] || humanize(id),
    status: (id) => STATUS[id] || { text: humanize(id) || "—", cls: "" },
  };
})();

/*
 * KARMA web UI -- Judge view.
 *
 * Two subviews: Runs (individual runs, judged per stage) and Batches
 * (directories grouping many runs, judged across runs). Each row can be
 * judged for real or dry-run via /api/judge/start, which returns a job id;
 * progress streams from /api/judge/jobs/<id>/stream into a live log.
 */
(function () {
  "use strict";
  const KARMA = window.KARMA;
  const { el, clear, api } = KARMA;

  let root;
  let sub = "runs";

  function errBox(e) { return el("div", { class: "error-box" }, e.message || String(e)); }
  function scoreCell(v) {
    if (v == null) return el("span", { class: "muted" }, "—");
    const cls = v >= 0.8 ? "ok" : v >= 0.5 ? "warn" : "bad";
    return el("span", { class: "badge " + cls }, v.toFixed(3));
  }
  function statusBadge(id) {
    if (!id) return el("span", { class: "muted" }, "—");
    const st = KARMA.labels.status(id);
    return el("span", { class: "badge " + st.cls }, st.text);
  }

  function mount(container) { root = container; render(); }

  function render() {
    clear(root);
    root.appendChild(el("h2", {}, "Judge"));
    root.appendChild(el("div", { class: "subtabs" },
      el("button", { class: "tab" + (sub === "runs" ? " active" : ""), onClick: () => { sub = "runs"; render(); } }, "Runs"),
      el("button", { class: "tab" + (sub === "batches" ? " active" : ""), onClick: () => { sub = "batches"; render(); } }, "Batches")));
    root.appendChild(el("pre", { class: "log", id: "judge-log" }, "Judge output appears here.\n"));
    if (sub === "runs") renderRuns();
    else renderBatches();
  }

  async function renderRuns() {
    const panel = el("div", { class: "panel" });
    root.appendChild(panel);
    try {
      const runs = await api.get("/api/judge/runs");
      const tbl = el("table", {}, el("thead", {}, el("tr", {},
        el("th", {}, "Run"), el("th", {}, "Status"), el("th", {}, "Judge"),
        el("th", {}, "Score"), el("th", {}, ""))));
      const body = el("tbody", {});
      for (const r of runs) {
        body.appendChild(el("tr", {},
          el("td", {}, r.run_id),
          el("td", {}, statusBadge(r.status)),
          el("td", {}, statusBadge(r.judge_status)),
          el("td", {}, scoreCell(r.judge_score)),
          el("td", {}, actionBtns("run", r.path))));
      }
      if (!runs.length) body.appendChild(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "No runs found.")));
      tbl.appendChild(body);
      panel.appendChild(tbl);
    } catch (e) { panel.appendChild(errBox(e)); }
  }

  async function renderBatches() {
    const panel = el("div", { class: "panel" });
    root.appendChild(panel);
    try {
      const batches = await api.get("/api/judge/batches");
      const tbl = el("table", {}, el("thead", {}, el("tr", {},
        el("th", {}, "Batch"), el("th", {}, "Runs"), el("th", {}, "Judged"),
        el("th", {}, "Avg score"), el("th", {}, ""))));
      const body = el("tbody", {});
      for (const b of batches) {
        body.appendChild(el("tr", {},
          el("td", {}, b.name),
          el("td", {}, String(b.run_count)),
          el("td", {}, String(b.judged_count)),
          el("td", {}, scoreCell(b.average_final_score)),
          el("td", {}, actionBtns("batch", b.batch_dir))));
      }
      if (!batches.length) body.appendChild(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "No batches found.")));
      tbl.appendChild(body);
      panel.appendChild(tbl);
    } catch (e) { panel.appendChild(errBox(e)); }
  }

  function actionBtns(targetType, targetPath) {
    return el("span", { class: "toolbar", style: "margin:0" },
      el("button", { class: "btn", onClick: () => startJudge(targetType, targetPath, false) }, "Judge"),
      el("button", { class: "btn secondary", onClick: () => startJudge(targetType, targetPath, true) }, "Dry run"));
  }

  async function startJudge(targetType, targetPath, dryRun) {
    const log = document.getElementById("judge-log");
    log.textContent = `${dryRun ? "Dry-" : ""}judging ${targetType}: ${targetPath}\n`;
    try {
      const { job_id } = await api.post("/api/judge/start", {
        target_type: targetType, target_path: targetPath, dry_run: dryRun,
      });
      log.textContent += "job " + job_id + "\n";
      api.stream(`/api/judge/jobs/${job_id}/stream`, {
        statusPath: `/api/judge/jobs/${job_id}`,
        onEvent: (ev) => {
          if (ev.type === "judge_progress") {
            const where = ev.stage_id ? `${ev.run_id}/${ev.stage_id}` : ev.run_id;
            log.textContent += `  ${where}: verdict=${ev.verdict ?? "-"} score=${ev.score ?? "-"}\n`;
          } else if (ev.type === "judge_complete") {
            log.textContent += `judge ${ev.status} — switch subtabs to refresh scores\n`;
          }
          log.scrollTop = log.scrollHeight;
        },
        onDone: () => { log.textContent += "— judge stream ended —\n"; },
      });
    } catch (e) { log.textContent += "Error: " + e.message + "\n"; }
  }

  KARMA.registerView({ id: "judge", label: "Judge", mount });
})();

# `runs/` layout convention

Keep this directory **clean and consistent**. Every run lives under:

```
runs/<suite>/<iteration>/<run_id>/
```

- **`<suite>`** — one of exactly: `examples`, `short`, `long`, `error-prone`.
  Never create a new top-level suite folder.
- **`<iteration>`** — `r0` (baseline), `r1`, `r2`, … for successive full/partial
  sweeps, plus `fixrun` for targeted re-runs. `r0` is the oldest, highest `rN` the
  newest.
- **`<run_id>`** — `<name>-<YYYYmmdd_HHMMSS>` (written by the orchestrator).

## Rules
- **No flat top-level run folders** (e.g. `runs/short-half`, `runs/examples-r5`).
  A batch always nests: set the dispatcher `folder` param to `"<suite>/<iteration>"`
  (the dispatcher supports the nested path via `--runs-dir runs/<folder>`).
- A partial sweep (a representative subset, not all workflows) still uses the same
  `rN` style — record "what it was" here, not in the folder name.
- Archive/scratch folders are prefixed with `_` (e.g. `runs/_archive/`); the web-UI
  catalog skips `_`-prefixed dirs.

## Iteration log
| Suite/iter | What it was |
|---|---|
| `examples/r0`–`r4` | full 100-case example sweeps (successive rounds) |
| `examples/r5` | all-100 example re-sweep (all-remote); 96% |
| `short/r0`–`r4` | short-workflow sweeps (successive rounds) |
| `short/r5` | **representative 300/522 short-workflow half** (stratified by service, 48% adversary ratio preserved); split heavy→remote, light→local |
| `long/r0`–`r4`, `error-prone/r0`–`r4` | long / error-prone sweeps |
| `<suite>/fixrun` | targeted re-runs of specific failures |

# Ray Cluster Change / Migration Plan

**Namespace:** `ray`
**Prepared:** 2026-06-18
**Status:** FOR REVIEW ONLY — no changes have been applied. All commands below
are proposed for the next upgrade window and MUST NOT be run until approved.

---

## 1. Current State (as inspected)

Inspected live with `kubectl -n ray get ...`. Findings:

### Deployments
| Resource     | Replicas | Image                  | CPU/Mem requests | CPU/Mem limits |
|--------------|----------|------------------------|------------------|----------------|
| `ray-head`   | 1        | `rayproject/ray:2.9.0` | none set (`{}`)  | none set (`{}`)|
| `ray-worker` | 1        | `rayproject/ray:2.9.0` | none set (`{}`)  | none set (`{}`)|

- `ray-head` container exposes port `6379` (`gcs`).
- `ray-worker` container exposes no named ports.
- Neither deployment declares any `resources.requests` or `resources.limits`
  (resources block is empty `{}`).

### Services
| Service    | Type      | Ports            | Notes                            |
|------------|-----------|------------------|----------------------------------|
| `ray-head` | ClusterIP | 6379/TCP (`gcs`) | Dashboard port 8265 NOT exposed  |

- Only the GCS port (6379) is published. The Ray dashboard (8265) is not
  currently exposed on the `ray-head` Service.

### Pods (running)
- `ray-head-5c586c8fd6-4m62c` (1/1 Running)
- `ray-worker-7d7d685797-59v7v` (1/1 Running)
- `ray-client` (1/1 Running)

---

## 2. Proposed Changes for the Next Upgrade Window

1. **Image upgrade:** bump Ray from `2.9.0` to a newer pinned release
   (e.g. `2.10.0`) on both `ray-head` and `ray-worker`, rolled out head-first.
2. **Worker scale-up:** increase `ray-worker` replicas from 1 to the target
   capacity (e.g. 3) once the new image is verified healthy.
3. **Resource governance:** add explicit CPU/memory `requests` and `limits` to
   both deployments (currently unset) so the scheduler can place and protect the
   pods (suggested: head 2 CPU / 4Gi, worker 1 CPU / 2Gi requests, with limits
   sized per workload review).
4. **Dashboard exposure:** publish the Ray dashboard port `8265` on the
   `ray-head` Service for observability during/after the migration.

Rollout order: upgrade image -> verify head & one worker healthy -> scale
workers -> apply resource specs -> expose dashboard. Each step is gated on the
prior being healthy; roll back by reverting the image / replica count if a step
fails.

---

## 3. Exact kubectl Commands (PROPOSED — DO NOT RUN NOW)

```bash
# --- Step 1: image upgrade (head first, then worker) ---
kubectl -n ray set image deploy/ray-head ray-head=rayproject/ray:2.10.0
kubectl -n ray rollout status deploy/ray-head
kubectl -n ray set image deploy/ray-worker ray-worker=rayproject/ray:2.10.0
kubectl -n ray rollout status deploy/ray-worker

# --- Step 2: scale workers up to target capacity ---
kubectl -n ray scale deploy/ray-worker --replicas=3
kubectl -n ray rollout status deploy/ray-worker

# --- Step 3: add resource requests/limits ---
kubectl -n ray patch deploy ray-head --type=json -p='[
  {"op":"add","path":"/spec/template/spec/containers/0/resources","value":
    {"requests":{"cpu":"2","memory":"4Gi"},"limits":{"cpu":"4","memory":"8Gi"}}}]'
kubectl -n ray patch deploy ray-worker --type=json -p='[
  {"op":"add","path":"/spec/template/spec/containers/0/resources","value":
    {"requests":{"cpu":"1","memory":"2Gi"},"limits":{"cpu":"2","memory":"4Gi"}}}]'

# --- Step 4: expose the dashboard (8265) on the ray-head Service ---
kubectl -n ray patch svc ray-head --type=json -p='[
  {"op":"add","path":"/spec/ports/-","value":
    {"name":"dashboard","port":8265,"targetPort":8265,"protocol":"TCP"}}]'

# --- Rollback (if needed) ---
kubectl -n ray rollout undo deploy/ray-head
kubectl -n ray rollout undo deploy/ray-worker
kubectl -n ray scale deploy/ray-worker --replicas=1
```

---

## 4. Constraints Honored

- This document is the only artifact produced. No image was changed, no resource
  was scaled, patched, restarted, or reconfigured. The live `ray` namespace
  remains exactly as inspected above.

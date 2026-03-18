# Ray Base Cases

This service contains the Ray base-functionality corpus only.

Current base cases:

- `cluster_ready`: deploy a healthy head/service/worker cluster
- `dashboard_reachable`: expose the Ray dashboard on the head Service
- `job_execution`: submit a sample Ray job and observe `pong`
- `worker_scaling`: scale workers to a target replica count
- `version_upgrade`: move the cluster from `from_image` to `to_image`
- `cluster_teardown`: remove Ray resources while keeping the namespace

Not included here:

- bad worker startup flags
- wrong client/job addresses
- missing dashboard ports as a reusable perturbation

Those belong in a future adversarial overlay/plugin layer rather than in the
base capability case definitions.

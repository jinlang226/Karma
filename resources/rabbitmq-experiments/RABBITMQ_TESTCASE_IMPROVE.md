# RabbitMQ Test Case Precondition Independence Audit

Date: 2026-02-27  
Updated: 2026-02-28

## Scope

- Reviewed `test.yaml` under `resources/rabbitmq-experiments/*`.
- Focus: identify precondition units that are not independent enough (composite gates spanning multiple resource groups).
- This document now tracks both open issues and recently completed fixes.

## Current Summary

- Cases reviewed: `8`
- Cases still containing at least one non-independent precondition unit: `6/8`
- Open non-independent units: `6`
- Recently fixed in code: `3` areas
  - `blue_green_migration` source/target precondition group split
  - `manual_policy_sync` unsynced-policy baseline split
  - `manual_monitoring` unsynced-target baseline split

## Open Findings

| Case | Non-Independent Unit | Why It Is Not Independent Enough | Location |
|---|---|---|---|
| classic_queue | `classic_queue_baseline_ready` | Composite baseline checker (`setup_precondition_check.py --min-ready`) bundles multiple resource states; `apply` is no-op (`true`). | `resources/rabbitmq-experiments/classic_queue/test.yaml` |
| failover | `failover_baseline_ready` | Composite baseline checker combines cluster/curl-test/cookie-drift validation in one gate; `apply` is no-op (`true`). | `resources/rabbitmq-experiments/failover/test.yaml` |
| manual_backup_restore | `backup_restore_baseline_ready` | Composite final-state checker overlaps restore/backup/PVC state in one gate; `apply` is no-op (`true`). | `resources/rabbitmq-experiments/manual_backup_restore/test.yaml` |
| manual_skip_upgrade | `skip_upgrade_baseline_ready` | Composite checker merges cluster/curl/queue seed/version checks in one gate; `apply` is no-op (`true`). | `resources/rabbitmq-experiments/manual_skip_upgrade/test.yaml` |
| manual_tls_rotation | `tls_rotation_baseline_ready` | Composite checker merges cluster readiness + TLS certificate baseline checks in one gate; `apply` is no-op (`true`). | `resources/rabbitmq-experiments/manual_tls_rotation/test.yaml` |
| manual_user_permission | `user_permission_baseline_ready` | Composite checker merges cluster/curl/client-failure/permission state in one gate; `apply` is no-op (`true`). | `resources/rabbitmq-experiments/manual_user_permission/test.yaml` |

## Completed Fixes (2026-02-28)

- `blue_green_migration`
  - Split broad preconditions into source/target-specific groups.
  - Added scoped setup-check modes:
    - `--bootstrap-source-only`
    - `--bootstrap-target-only`
    - `--seed-config-source-only`
    - `--seed-config-target-only`
    - `--seed-data-source-only`
  - Separated seed config creation from seed data job execution.
- `manual_policy_sync`
  - Replaced composite gate with `policy_unsynced_state_ready`.
  - Added scoped check mode `--policy-unsynced-only`.
  - `apply` now actively reconciles unsynced baseline by clearing `/app` policy `ha-all`.
- `manual_monitoring`
  - Replaced composite gate with `monitoring_targets_unsynced_ready`.
  - Added scoped check mode `--targets-unsynced-only`.
  - `apply` keeps an explicit state-reconcile path by reapplying/restarting Prometheus deployment.

## Improvement Direction

- Continue replacing each remaining `*_baseline_ready` with narrow units per resource group (single concern per unit).
- Keep `setup_precondition_check.py` modes scoped and directly mapped to exactly one precondition unit.
- Avoid no-op baseline units (`apply: true`) that validate many concerns at once; attach actionable `apply` to the same resource group verified by probe/verify.

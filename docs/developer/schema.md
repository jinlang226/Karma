# Test Case Schema (test.yaml)

This document describes the YAML schema for a single benchmark test case (the `test.yaml` file under `resources/<service>/<case>/`).

The schema is intentionally minimal and composable. Fields are validated at load time and during workflow runtime setup.

## Required Top-Level Fields (Recommended for all cases)

These fields are required for a meaningful benchmark case. The loader itself does not enforce all of them, but the
runtime assumes they exist for correct behavior.

- `type`: string. Case type identifier (free-form but stable within a service).
- `targetApp`: string. Target app name, used for UI grouping and metadata.
- `numAppInstance`: integer. Used for metadata/UI; set to `0` if not applicable.
- `maxAttempts`: integer. Maximum submit attempts for the case.
- `preconditionUnits`: list. Each unit must include `probe`, `apply`, `verify`.
- `oracle.verify.commands`: list. The canonical verification logic.
- `cleanUpCommands`: list. Cleanup commands for the case.
- `detailedInstructions`: string block. Human-facing instructions.
- `operatorContext`: string block. Context and hints for the operator/agent.

If you do not need a field (e.g., no cleanup), keep it but set it to an empty list to keep schema consistent.

## Optional / Advanced Top-Level Fields

- `params`: parameter definitions and defaults for templating (`{{params.<name>}}`).
- `namespace_contract`: declare namespace roles required by the case (especially for workflows).
- `preOperationCommands`: list of commands run before solve.
- `setup_self_check`: override the derived precondition check.

Unsupported legacy fields (rejected when a case is executed in runner/orchestrator):
- `precondition_units` (use `preconditionUnits`)
- `verificationCommands` (use `oracle.verify.commands`)
- `verificationHooks` / `verification_hooks` (use `oracle.verify.hooks`)
- `referenceSolutionCommands` / `reference_solution_commands` (remove)

## Command Item Schema

All command lists use the same item schema:

```yaml
- command: ["kubectl", "get", "pods"]   # or a shell string
  timeout_sec: 20                        # optional
  sleep: 2                               # optional
  namespace_role: source                 # optional (workflow namespace role)
```

Notes:
- `command` can be a list (preferred) or a string.
- `namespace_role` is used to bind a command to a workflow namespace role.

## Parameters

`params.definitions` declares all parameters used in the case. Parameters are referenced with
`{{params.<name>}}` anywhere in the YAML.

Supported types:
- `string` (default)
- `int`
- `float` / `number`
- `bool`
- `enum` (requires `values`)
- `duration` (string)
- `quantity` (string)

Optional constraints:
- `required: true`
- `min`, `max` (for numeric types)
- `pattern` (regex)

Example:

```yaml
params:
  definitions:
    target_value:
      type: string
      default: x
    retries:
      type: int
      default: 3
      min: 1
      max: 10
    mode:
      type: enum
      values: [fast, safe]
      default: safe
```

## Namespace Contract (workflow roles)

Use `namespace_contract` when a case must touch multiple namespaces in workflow mode.

```yaml
namespace_contract:
  default_role: source
  required_roles:
  - source
  - target
  role_ownership:
    source: framework
    target: framework
```

At runtime, these roles map to env vars:
- `BENCH_NS_SOURCE`, `BENCH_NS_TARGET`, ...
- `BENCH_NAMESPACE` is the default role namespace

`role_ownership` is optional:
- `framework` (default): framework pre-creates namespace before setup.
- `case`: framework binds/injects namespace name but does not pre-create it.

Notes:
- `role_ownership` keys must be declared roles (`required_roles`, `optional_roles`, or `default_role`).
- Allowed values are only `framework` and `case`.
- Final cleanup still attempts namespace deletion for all bound roles (`ignore-not-found`).

## Precondition Units

Each precondition unit is a deterministic setup gate with probe/apply/verify commands.

```yaml
preconditionUnits:
  - id: configmap_ready
    probe:
      commands:
        - command: ["kubectl", "get", "configmap", "demo-config"]
          timeout_sec: 20
    apply:
      commands:
        - command: ["kubectl", "apply", "-f", "resources/.../config.yaml"]
          timeout_sec: 20
    verify:
      commands:
        - command: ["kubectl", "get", "configmap", "demo-config"]
          timeout_sec: 20
      retries: 5
      interval_sec: 2
```

Rules:
- `probe`, `apply`, and `verify` are required for each unit.
- `verify.retries` and `verify.interval_sec` are optional (defaults: 1, 0).

## Oracle

The oracle defines the invariant checks for the case.

```yaml
oracle:
  verify:
    commands:
      - command: ["python3", "oracle.py", "--expected", "{{params.target_value}}"]
        timeout_sec: 20
```

Optional verification hooks:

```yaml
oracle:
  verify:
    commands: [...]
    hooks:
      before_commands:
      - command: ["kubectl", "apply", "-f", "oracle-client.yaml"]
      after_commands:
      - command: ["kubectl", "delete", "-f", "oracle-client.yaml", "--ignore-not-found=true"]
      after_failure_mode: warn   # warn | fail
```

## Cleanup

```yaml
cleanUpCommands:
  - command: ["kubectl", "delete", "configmap", "demo-config", "--ignore-not-found=true"]
    timeout_sec: 20
```

## Minimal Example (single namespace)

```yaml
type: demo-configmap-update
targetApp: local
numAppInstance: 0
maxAttempts: 1

params:
  definitions:
    target_value:
      type: string
      default: x

preconditionUnits:
  - id: configmap_ready
    probe:
      commands:
        - command: ["kubectl", "get", "configmap", "demo-config"]
          timeout_sec: 20
    apply:
      commands:
        - command: ["kubectl", "apply", "-f", "resources/demo/configmap-update/resource/config.yaml"]
          timeout_sec: 20
    verify:
      commands:
        - command: ["kubectl", "get", "configmap", "demo-config"]
          timeout_sec: 20
      retries: 5
      interval_sec: 2

detailedInstructions: |
  Update ConfigMap `demo-config` in the assigned namespace.

  Requirement:
  - Set `data.value={{params.target_value}}`.
  - Do not create or delete the ConfigMap during solve; only update its value.

operatorContext: |
  Namespace: ${BENCH_NAMESPACE}
  kubectl -n ${BENCH_NAMESPACE} get configmap demo-config -o yaml

oracle:
  verify:
    commands:
      - command: ["python3", "resources/demo/configmap-update/oracle/oracle.py", "--expected-value", "{{params.target_value}}"]
        timeout_sec: 20

cleanUpCommands:
  - command: ["kubectl", "delete", "configmap", "demo-config", "--ignore-not-found=true"]
    timeout_sec: 20
```

## Example (two namespaces)

```yaml
type: demo-configmap-update-two-ns
targetApp: local
numAppInstance: 0
maxAttempts: 1

params:
  definitions:
    target_value:
      type: string
      default: x

namespace_contract:
  default_role: source
  required_roles:
  - source
  - target

preconditionUnits:
  - id: configmap_ready
    probe:
      commands:
        - command: ["kubectl", "-n", "${BENCH_NS_SOURCE}", "get", "configmap", "demo-config"]
          timeout_sec: 20
        - command: ["kubectl", "-n", "${BENCH_NS_TARGET}", "get", "configmap", "demo-config"]
          timeout_sec: 20
    apply:
      commands:
        - command: ["kubectl", "-n", "${BENCH_NS_SOURCE}", "apply", "-f", "resources/demo/configmap-update-two-ns/resource/config.yaml"]
          timeout_sec: 20
        - command: ["kubectl", "-n", "${BENCH_NS_TARGET}", "apply", "-f", "resources/demo/configmap-update-two-ns/resource/config.yaml"]
          timeout_sec: 20
    verify:
      commands:
        - command: ["kubectl", "-n", "${BENCH_NS_SOURCE}", "get", "configmap", "demo-config"]
          timeout_sec: 20
        - command: ["kubectl", "-n", "${BENCH_NS_TARGET}", "get", "configmap", "demo-config"]
          timeout_sec: 20
      retries: 5
      interval_sec: 2

detailedInstructions: |
  Update ConfigMap `demo-config` in both assigned namespaces.

  Requirement:
  - Set `data.value={{params.target_value}}` in each namespace.
  - Do not create or delete the ConfigMap during solve; only update its value.

operatorContext: |
  Namespaces:
  - source: ${BENCH_NS_SOURCE}
  - target: ${BENCH_NS_TARGET}

  kubectl -n ${BENCH_NS_SOURCE} get configmap demo-config -o yaml
  kubectl -n ${BENCH_NS_TARGET} get configmap demo-config -o yaml

oracle:
  verify:
    commands:
      - command: ["python3", "resources/demo/configmap-update-two-ns/oracle/oracle.py", "--expected-value", "{{params.target_value}}", "--namespace", "${BENCH_NS_SOURCE}"]
        timeout_sec: 20
      - command: ["python3", "resources/demo/configmap-update-two-ns/oracle/oracle.py", "--expected-value", "{{params.target_value}}", "--namespace", "${BENCH_NS_TARGET}"]
        timeout_sec: 20

cleanUpCommands:
  - command: ["kubectl", "-n", "${BENCH_NS_SOURCE}", "delete", "configmap", "demo-config", "--ignore-not-found=true"]
    timeout_sec: 20
  - command: ["kubectl", "-n", "${BENCH_NS_TARGET}", "delete", "configmap", "demo-config", "--ignore-not-found=true"]
    timeout_sec: 20
```

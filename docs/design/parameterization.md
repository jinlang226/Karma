# Test Case Parameterization

## Overview

Parameterization allows a single test case definition to represent an
entire family of related tasks.

Instead of duplicating test cases for small configuration differences
(e.g., upgrading from 3.5→3.6 vs 3.6→3.7), KARMA supports template-based
test cases whose behavior is determined by structured parameters.

Parameterization enables:

-   Reduced benchmark duplication
-   Large version matrix coverage
-   Task variation across environments/targets
-   Scalable workflow composition

------------------------------------------------------------------------

## Motivation

Consider two test cases:

-   Upgrade RabbitMQ from 3.5 → 3.6
-   Upgrade RabbitMQ from 3.6 → 3.7

The logic is identical. Only the version numbers differ.

Without parameterization:

-   Two separate test files are required
-   Maintenance overhead increases
-   Workflow composition becomes verbose
-   Version matrices become difficult to manage

Parameterization allows this to be expressed as a single template using:

-   `from_version`
-   `to_version`

------------------------------------------------------------------------

## Core Model

Each test case may define parameters:

``` yaml
params:
  definitions:
    from_version:
      type: string
      required: true
    to_version:
      type: string
      required: true
    replica_count:
      type: int
      default: 3
      min: 1
      max: 10
```

Parameters are referenced using template substitution:

-   `{{params.from_version}}`
-   `{{params.replica_count}}`

Substitution may occur in:

-   Preconditions
-   Oracle verify logic
-   Operator instructions/context

------------------------------------------------------------------------

## Parameter Types and Validation

Supported parameter types include:

-   `string`
-   `int`
-   `float`
-   `bool`
-   `enum`
-   `duration`
-   `quantity`

Validation rules may include:

-   Required fields
-   Default values
-   Min / max bounds
-   Regex patterns
-   Enumerated choices

Example:

``` yaml
replica_count:
  type: int
  default: 3
  min: 1
  max: 10
```

Validation ensures runtime overrides remain within a declared parameter
domain.

------------------------------------------------------------------------

## Runtime Overrides

Parameters may be:

1.  Defined statically in the test case
2.  Overridden at runtime via workflow stage `param_overrides`
3.  Injected via workflow configuration

Example workflow override:

```yaml
spec:
  stages:
    - id: upgrade_a
      service: rabbitmq-experiments
      case: manual_skip_upgrade
      param_overrides:
        from_version: "3.6"
        to_version: "3.9"
```

Overrides are validated before execution.

------------------------------------------------------------------------

## Parameterization and Oracle

Parameterization directly affects Oracle verification behavior.

`oracle.verify()` must be parameter-aware.

Example:

``` yaml
oracle:
  verify:
    commands:
      - python3 oracle.py --expected-version {{params.to_version}}
```

The invariant is defined relative to parameter values.

------------------------------------------------------------------------

## Parameter Domain Design

Parameter domains should be chosen so the case remains meaningful and
verifiable across supported values.

Some parameter combinations may be invalid:

-   Direct upgrade 3.6 → 4.2 unsupported
-   Storage engine transitions require reinstall
-   Feature flags create incompatible states

To preserve determinism:

1.  Exclude unsupported parameter regions
2.  Explicitly declare unsupported combinations
3.  Use stage-specific preconditions to normalize required baseline state

------------------------------------------------------------------------

## Interaction with Workflow Runtime

During workflow runtime:

-   Parameters are resolved
-   Preconditions run using `probe -> apply -> verify`
-   `oracle.verify()` checks stage correctness with resolved parameters

------------------------------------------------------------------------

## Composability

Parameterization enables multi-stage workflows such as:

-   Stage 1: 3.6 → 3.7
-   Stage 2: 3.7 → 4.0

This avoids duplicating test definitions while preserving deterministic
evaluation.

------------------------------------------------------------------------

## Summary

Test case parameterization enables KARMA to:

-   Generalize test logic across configuration spaces
-   Reduce duplication
-   Support version matrices
-   Improve workflow composability

By integrating parameter resolution into preconditions and Oracle
verification logic, KARMA maintains deterministic evaluation while
allowing flexible task definitions across diverse operational scenarios.

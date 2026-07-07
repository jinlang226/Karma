#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/cockroachdb-monitoring-ingress.yaml

plan_stage "stage_01" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_02" "cockroachdb/expose-ingress.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/version-check.sh"

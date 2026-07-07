#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/cockroachdb-enterprise-lifecycle-adversary.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/generate-cert.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"
plan_stage "stage_05" "cockroachdb/partitioned-update.sh"
plan_stage "stage_06" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_07" "cockroachdb/version-check.sh"
plan_stage "stage_08" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_09" "cockroachdb/expose-ingress.sh"

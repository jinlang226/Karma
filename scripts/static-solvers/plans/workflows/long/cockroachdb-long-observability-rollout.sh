#!/usr/bin/env bash
# Generated from workflows/long/cockroachdb-long-observability-rollout.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_05" "cockroachdb/expose-ingress.sh"
plan_stage "stage_06" "cockroachdb/zone-config.sh"
plan_stage "stage_07" "cockroachdb/zone-config.sh"
plan_stage "stage_08" "cockroachdb/zone-config.sh"
plan_stage "stage_09" "cockroachdb/cluster-settings.sh"
plan_stage "stage_10" "cockroachdb/version-check.sh"
plan_stage "stage_11" "cockroachdb/partitioned-update.sh"

#!/usr/bin/env bash
# Generated from workflows/long/cockroachdb-long-storage-management-adversary.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/zone-config.sh"
plan_stage "stage_04" "cockroachdb/zone-config.sh"
plan_stage "stage_05" "cockroachdb/zone-config.sh"
plan_stage "stage_06" "cockroachdb/zone-config.sh"
plan_stage "stage_07" "cockroachdb/zone-config.sh"
plan_stage "stage_08" "cockroachdb/cluster-settings.sh"
plan_stage "stage_09" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_10" "cockroachdb/version-check.sh"
plan_stage "stage_11" "cockroachdb/health-check-recovery.sh"
plan_stage "stage_12" "cockroachdb/decommission.sh"

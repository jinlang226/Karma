#!/usr/bin/env bash
# Generated from workflows/long/cockroachdb-long-recovery-marathon-adversary.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/generate-cert.sh"
plan_stage "stage_04" "cockroachdb/health-check-recovery.sh"
plan_stage "stage_05" "cockroachdb/cluster-settings.sh"
plan_stage "stage_06" "cockroachdb/cluster-settings.sh"
plan_stage "stage_07" "cockroachdb/zone-config.sh"
plan_stage "stage_08" "cockroachdb/zone-config.sh"
plan_stage "stage_09" "cockroachdb/certificate-rotation.sh"
plan_stage "stage_10" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_11" "cockroachdb/partitioned-update.sh"
plan_stage "stage_12" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_13" "cockroachdb/version-check.sh"

#!/usr/bin/env bash
# Generated from workflows/error-prone/cockroachdb-four-settings-zone-upgrade-audit-rollback.yaml

plan_stage "stage_01" "cockroachdb/cluster-settings.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"
plan_stage "stage_05" "cockroachdb/zone-config.sh"
plan_stage "stage_06" "cockroachdb/partitioned-update.sh"
plan_stage "stage_07" "cockroachdb/readonly-audit.sh"
plan_stage "stage_08" "cockroachdb/rollback-rehearsal.sh"

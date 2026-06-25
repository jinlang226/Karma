#!/usr/bin/env bash
# Generated from workflows/error-prone/cockroachdb-upgrade-zone-rebalance-audit-rollback.yaml

plan_stage "stage_01" "cockroachdb/partitioned-update.sh"
plan_stage "stage_02" "cockroachdb/zone-config.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/readonly-audit.sh"
plan_stage "stage_05" "cockroachdb/rollback-rehearsal.sh"

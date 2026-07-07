#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-zone-rebalance-upgrade-rollback.yaml

plan_stage "stage_01" "cockroachdb/zone-config.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/partitioned-update.sh"
plan_stage "stage_04" "cockroachdb/rollback-rehearsal.sh"

#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-partitioned-upgrade-rebalance-audit.yaml

plan_stage "stage_01" "cockroachdb/partitioned-update.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/readonly-audit.sh"

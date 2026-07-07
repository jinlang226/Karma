#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-upgrade-rebalance-audit-change-plan.yaml

plan_stage "stage_01" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/readonly-audit.sh"
plan_stage "stage_04" "cockroachdb/change-plan-only.sh"

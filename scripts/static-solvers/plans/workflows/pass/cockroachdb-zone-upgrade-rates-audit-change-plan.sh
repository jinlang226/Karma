#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-zone-upgrade-rates-audit-change-plan.yaml

plan_stage "stage_01" "cockroachdb/zone-config.sh"
plan_stage "stage_02" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"
plan_stage "stage_05" "cockroachdb/readonly-audit.sh"
plan_stage "stage_06" "cockroachdb/change-plan-only.sh"

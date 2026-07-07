#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-upgrade-five-settings-rollback-change-plan.yaml

plan_stage "stage_01" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"
plan_stage "stage_05" "cockroachdb/cluster-settings.sh"
plan_stage "stage_06" "cockroachdb/cluster-settings.sh"
plan_stage "stage_07" "cockroachdb/rollback-rehearsal.sh"
plan_stage "stage_08" "cockroachdb/change-plan-only.sh"

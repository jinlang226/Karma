#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-long-partitioned-version-sweep.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/partitioned-update.sh"
plan_stage "stage_04" "cockroachdb/version-check.sh"
plan_stage "stage_05" "cockroachdb/partitioned-update.sh"
plan_stage "stage_06" "cockroachdb/version-check.sh"
plan_stage "stage_07" "cockroachdb/partitioned-update.sh"
plan_stage "stage_08" "cockroachdb/version-check.sh"
plan_stage "stage_09" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_10" "cockroachdb/version-check.sh"
plan_stage "stage_11" "cockroachdb/cluster-settings.sh"

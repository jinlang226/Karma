#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/platform-upgrade-day-ext.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/partitioned-update.sh"
plan_stage "stage_04" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_05" "mongodb/deploy.sh"
plan_stage "stage_06" "mongodb/initialize.sh"
plan_stage "stage_07" "mongodb/version-upgrade.sh"
plan_stage "stage_08" "elasticsearch/deploy-core-cluster.sh"

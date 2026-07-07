#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-upgrade-decommission.yaml

plan_stage "stage_01" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_02" "cockroachdb/partitioned-update.sh"
plan_stage "stage_03" "cockroachdb/version-check.sh"
plan_stage "stage_04" "cockroachdb/decommission.sh"
plan_stage "stage_05" "cockroachdb/zone-config.sh"

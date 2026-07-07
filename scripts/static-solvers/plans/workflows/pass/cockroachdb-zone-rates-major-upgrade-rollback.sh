#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-zone-rates-major-upgrade-rollback.yaml

plan_stage "stage_01" "cockroachdb/zone-config.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_05" "cockroachdb/rollback-rehearsal.sh"

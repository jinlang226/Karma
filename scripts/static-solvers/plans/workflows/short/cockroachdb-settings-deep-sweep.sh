#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-settings-deep-sweep.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"
plan_stage "stage_05" "cockroachdb/cluster-settings.sh"
plan_stage "stage_06" "cockroachdb/cluster-settings.sh"
plan_stage "stage_07" "cockroachdb/cluster-settings.sh"
plan_stage "stage_08" "cockroachdb/zone-config.sh"
plan_stage "stage_09" "cockroachdb/zone-config.sh"
plan_stage "stage_10" "cockroachdb/monitoring-integration.sh"

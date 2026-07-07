#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-full-lifecycle.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/zone-config.sh"
plan_stage "stage_05" "cockroachdb/version-check.sh"
plan_stage "stage_06" "cockroachdb/monitoring-integration.sh"

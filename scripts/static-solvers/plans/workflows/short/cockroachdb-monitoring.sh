#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-monitoring.yaml

plan_stage "stage_01" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/version-check.sh"

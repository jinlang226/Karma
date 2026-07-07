#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-zone-config-observe.yaml

plan_stage "stage_01" "cockroachdb/zone-config.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/monitoring-integration.sh"

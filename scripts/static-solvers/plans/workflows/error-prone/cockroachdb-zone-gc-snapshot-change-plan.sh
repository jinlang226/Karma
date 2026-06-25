#!/usr/bin/env bash
# Generated from workflows/error-prone/cockroachdb-zone-gc-snapshot-change-plan.yaml

plan_stage "stage_01" "cockroachdb/zone-config.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/change-plan-only.sh"

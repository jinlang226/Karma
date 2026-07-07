#!/usr/bin/env bash
# Generated from workflows/error-prone/cockroachdb-major-upgrade-rebalance-audit.yaml

plan_stage "stage_01" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/readonly-audit.sh"

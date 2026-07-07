#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-rebalance-major-upgrade-change-plan.yaml

plan_stage "stage_01" "cockroachdb/cluster-settings.sh"
plan_stage "stage_02" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_03" "cockroachdb/change-plan-only.sh"

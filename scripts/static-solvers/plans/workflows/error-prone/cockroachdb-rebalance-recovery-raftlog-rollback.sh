#!/usr/bin/env bash
# Generated from workflows/error-prone/cockroachdb-rebalance-recovery-raftlog-rollback.yaml

plan_stage "stage_01" "cockroachdb/cluster-settings.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/rollback-rehearsal.sh"

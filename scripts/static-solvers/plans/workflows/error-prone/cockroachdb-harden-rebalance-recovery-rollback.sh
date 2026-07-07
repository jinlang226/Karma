#!/usr/bin/env bash
# Generated from workflows/error-prone/cockroachdb-harden-rebalance-recovery-rollback.yaml

plan_stage "stage_01" "cockroachdb/cluster-settings.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/rollback-rehearsal.sh"

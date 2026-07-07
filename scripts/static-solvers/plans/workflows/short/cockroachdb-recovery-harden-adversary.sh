#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-recovery-harden-adversary.yaml

plan_stage "stage_01" "cockroachdb/health-check-recovery.sh"
plan_stage "stage_02" "cockroachdb/zone-config.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_05" "cockroachdb/version-check.sh"

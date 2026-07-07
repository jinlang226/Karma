#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-health-recovery.yaml

plan_stage "stage_01" "cockroachdb/health-check-recovery.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/version-check.sh"

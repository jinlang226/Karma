#!/usr/bin/env bash
# Generated from workflows/error-prone/cockroachdb-zone-partitioned-upgrade-audit.yaml

plan_stage "stage_01" "cockroachdb/zone-config.sh"
plan_stage "stage_02" "cockroachdb/partitioned-update.sh"
plan_stage "stage_03" "cockroachdb/readonly-audit.sh"

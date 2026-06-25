#!/usr/bin/env bash
# Generated from workflows/error-prone/cockroachdb-upgrade-zone-audit-rollback.yaml

plan_stage "stage_01" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_02" "cockroachdb/zone-config.sh"
plan_stage "stage_03" "cockroachdb/readonly-audit.sh"
plan_stage "stage_04" "cockroachdb/rollback-rehearsal.sh"

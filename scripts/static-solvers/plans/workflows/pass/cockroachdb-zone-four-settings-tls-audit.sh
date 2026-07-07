#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-zone-four-settings-tls-audit.yaml

plan_stage "stage_01" "cockroachdb/zone-config.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"
plan_stage "stage_05" "cockroachdb/cluster-settings.sh"
plan_stage "stage_06" "cockroachdb/generate-cert.sh"
plan_stage "stage_07" "cockroachdb/readonly-audit.sh"

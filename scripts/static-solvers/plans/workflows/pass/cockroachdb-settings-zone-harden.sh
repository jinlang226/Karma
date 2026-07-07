#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-settings-zone-harden.yaml

plan_stage "stage_01" "cockroachdb/cluster-settings.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/zone-config.sh"
plan_stage "stage_04" "cockroachdb/zone-config.sh"
plan_stage "stage_05" "cockroachdb/version-check.sh"

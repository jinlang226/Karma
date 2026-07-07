#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-five-settings-rollback.yaml

plan_stage "stage_01" "cockroachdb/cluster-settings.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"
plan_stage "stage_05" "cockroachdb/cluster-settings.sh"
plan_stage "stage_06" "cockroachdb/rollback-rehearsal.sh"

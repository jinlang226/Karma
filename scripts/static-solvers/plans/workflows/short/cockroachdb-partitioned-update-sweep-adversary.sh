#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-partitioned-update-sweep-adversary.yaml

plan_stage "stage_01" "cockroachdb/partitioned-update.sh"
plan_stage "stage_02" "cockroachdb/partitioned-update.sh"
plan_stage "stage_03" "cockroachdb/version-check.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"

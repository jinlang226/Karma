#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-major-upgrade-adversary.yaml

plan_stage "stage_01" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_02" "cockroachdb/version-check.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"

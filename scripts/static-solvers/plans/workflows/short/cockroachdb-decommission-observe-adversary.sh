#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-decommission-observe-adversary.yaml

plan_stage "stage_01" "cockroachdb/decommission.sh"
plan_stage "stage_02" "cockroachdb/zone-config.sh"
plan_stage "stage_03" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_04" "cockroachdb/expose-ingress.sh"
plan_stage "stage_05" "cockroachdb/cluster-settings.sh"
plan_stage "stage_06" "cockroachdb/version-check.sh"

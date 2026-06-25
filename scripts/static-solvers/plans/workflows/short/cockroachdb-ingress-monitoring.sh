#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-ingress-monitoring.yaml

plan_stage "stage_01" "cockroachdb/expose-ingress.sh"
plan_stage "stage_02" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/version-check.sh"

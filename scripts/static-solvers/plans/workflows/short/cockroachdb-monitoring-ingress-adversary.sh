#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-monitoring-ingress-adversary.yaml

plan_stage "stage_01" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_02" "cockroachdb/expose-ingress.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/version-check.sh"

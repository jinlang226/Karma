#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-tls-monitoring-adversary.yaml

plan_stage "stage_01" "cockroachdb/cluster-settings.sh"
plan_stage "stage_02" "cockroachdb/version-check.sh"
plan_stage "stage_03" "cockroachdb/generate-cert.sh"
plan_stage "stage_04" "cockroachdb/monitoring-integration.sh"

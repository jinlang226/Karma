#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-full-secure-lifecycle.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/generate-cert.sh"
plan_stage "stage_04" "cockroachdb/certificate-rotation.sh"
plan_stage "stage_05" "cockroachdb/cluster-settings.sh"
plan_stage "stage_06" "cockroachdb/zone-config.sh"
plan_stage "stage_07" "cockroachdb/version-check.sh"

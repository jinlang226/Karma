#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-cert-rotation-audit.yaml

plan_stage "stage_01" "cockroachdb/certificate-rotation.sh"
plan_stage "stage_02" "cockroachdb/version-check.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/zone-config.sh"

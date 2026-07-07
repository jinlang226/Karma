#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-cert-rotation-adversary.yaml

plan_stage "stage_01" "cockroachdb/certificate-rotation.sh"
plan_stage "stage_02" "cockroachdb/cluster-settings.sh"
plan_stage "stage_03" "cockroachdb/version-check.sh"

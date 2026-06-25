#!/usr/bin/env bash
# Generated from workflows/short/cockroachdb-upgrade-ingress-adversary.yaml

plan_stage "stage_01" "cockroachdb/version-check.sh"
plan_stage "stage_02" "cockroachdb/major-upgrade-finalize.sh"
plan_stage "stage_03" "cockroachdb/expose-ingress.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"

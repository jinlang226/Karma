#!/usr/bin/env bash
# Generated from workflows/pass/cockroachdb-deploy-init-decommission-adversary.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/cluster-settings.sh"
plan_stage "stage_04" "cockroachdb/decommission.sh"
plan_stage "stage_05" "cockroachdb/version-check.sh"

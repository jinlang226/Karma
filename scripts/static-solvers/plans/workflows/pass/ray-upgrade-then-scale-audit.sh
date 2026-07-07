#!/usr/bin/env bash
# Generated from workflows/pass/ray-upgrade-then-scale-audit.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/upgrade_version.sh"
plan_stage "stage_03" "ray/scale_workers.sh"
plan_stage "stage_04" "ray/readonly-audit.sh"

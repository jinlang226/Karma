#!/usr/bin/env bash
# Generated from workflows/error-prone/ray-scale-dashboard-config-audit.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/scale_workers.sh"
plan_stage "stage_03" "ray/dashboard_exposure.sh"
plan_stage "stage_04" "ray/readonly-audit.sh"

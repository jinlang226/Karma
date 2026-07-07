#!/usr/bin/env bash
# Generated from workflows/error-prone/ray-scale-dashboard-upgrade-next-upgrade-plan.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/scale_workers.sh"
plan_stage "stage_03" "ray/dashboard_exposure.sh"
plan_stage "stage_04" "ray/upgrade_version.sh"
plan_stage "stage_05" "ray/change-plan-only.sh"

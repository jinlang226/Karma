#!/usr/bin/env bash
# Generated from workflows/short/ray-recovery-upgrade-dashboard.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/worker_recovery.sh"
plan_stage "stage_03" "ray/upgrade_version.sh"
plan_stage "stage_04" "ray/dashboard_exposure.sh"
plan_stage "stage_05" "ray/job_submission.sh"

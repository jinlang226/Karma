#!/usr/bin/env bash
# Generated from workflows/error-prone/ray-full-chain-job-change-plan-redo.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/scale_workers.sh"
plan_stage "stage_03" "ray/upgrade_version.sh"
plan_stage "stage_04" "ray/dashboard_exposure.sh"
plan_stage "stage_05" "ray/job_submission.sh"
plan_stage "stage_06" "ray/change-plan-only.sh"

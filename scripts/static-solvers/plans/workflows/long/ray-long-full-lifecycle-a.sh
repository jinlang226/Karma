#!/usr/bin/env bash
# Generated from workflows/long/ray-long-full-lifecycle-a.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/dashboard_exposure.sh"
plan_stage "stage_03" "ray/job_submission.sh"
plan_stage "stage_04" "ray/scale_workers.sh"
plan_stage "stage_05" "ray/scale_workers.sh"
plan_stage "stage_06" "ray/job_submission.sh"
plan_stage "stage_07" "ray/upgrade_version.sh"
plan_stage "stage_08" "ray/scale_workers.sh"
plan_stage "stage_09" "ray/worker_recovery.sh"
plan_stage "stage_10" "ray/job_submission.sh"
plan_stage "stage_11" "ray/teardown_cluster.sh"

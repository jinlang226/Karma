#!/usr/bin/env bash
# Generated from workflows/short/ray-dashboard-job-adversary.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/dashboard_exposure.sh"
plan_stage "stage_03" "ray/job_submission.sh"
plan_stage "stage_04" "ray/teardown_cluster.sh"

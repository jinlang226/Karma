#!/usr/bin/env bash
# Generated from workflows/short/ray-upgrade-recovery-teardown.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/upgrade_version.sh"
plan_stage "stage_03" "ray/worker_recovery.sh"
plan_stage "stage_04" "ray/job_submission.sh"
plan_stage "stage_05" "ray/teardown_cluster.sh"

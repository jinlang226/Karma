#!/usr/bin/env bash
# Generated from workflows/short/ray-recovery-first-adversary.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/worker_recovery.sh"
plan_stage "stage_03" "ray/scale_workers.sh"
plan_stage "stage_04" "ray/job_submission.sh"
plan_stage "stage_05" "ray/teardown_cluster.sh"

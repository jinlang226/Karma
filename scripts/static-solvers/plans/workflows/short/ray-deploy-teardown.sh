#!/usr/bin/env bash
# Generated from workflows/short/ray-deploy-teardown.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/job_submission.sh"
plan_stage "stage_03" "ray/teardown_cluster.sh"

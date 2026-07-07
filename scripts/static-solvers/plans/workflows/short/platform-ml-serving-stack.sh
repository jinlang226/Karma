#!/usr/bin/env bash
# Generated from workflows/short/platform-ml-serving-stack.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/dashboard_exposure.sh"
plan_stage "stage_03" "ray/job_submission.sh"
plan_stage "stage_04" "ray/scale_workers.sh"
plan_stage "stage_05" "spark/deploy_spark_pi.sh"
plan_stage "stage_06" "spark/spark_data_skew.sh"
plan_stage "stage_07" "spark/spark_runtime_ops.sh"
plan_stage "stage_08" "mongodb/deploy.sh"
plan_stage "stage_09" "mongodb/initialize.sh"
plan_stage "stage_10" "mongodb/user-management.sh"

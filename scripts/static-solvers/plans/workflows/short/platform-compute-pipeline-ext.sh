#!/usr/bin/env bash
# Generated from workflows/short/platform-compute-pipeline-ext.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/dashboard_exposure.sh"
plan_stage "stage_03" "ray/job_submission.sh"
plan_stage "stage_04" "ray/scale_workers.sh"
plan_stage "stage_05" "ray/worker_recovery.sh"
plan_stage "stage_06" "spark/deploy_spark_pi.sh"
plan_stage "stage_07" "spark/spark_data_skew.sh"
plan_stage "stage_08" "spark/spark_runtime_ops.sh"
plan_stage "stage_09" "spark/spark_multi_tenant.sh"
plan_stage "stage_10" "spark/spark_streaming_autoscale.sh"

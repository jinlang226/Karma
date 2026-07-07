#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/spark-deep-tuning.yaml

plan_stage "stage_01" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_02" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_03" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_04" "spark/spark_runtime_ops.sh"
plan_stage "stage_05" "spark/spark_runtime_ops.sh"
plan_stage "stage_06" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_07" "spark/spark_streaming_autoscale.sh"

#!/usr/bin/env bash
# Generated from workflows/pass/spark-long-scale-campaign.yaml

plan_stage "stage_01" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_02" "spark/spark_data_skew.sh"
plan_stage "stage_03" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_04" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_05" "spark/spark_data_skew.sh"
plan_stage "stage_06" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_07" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_08" "spark/spark_data_skew.sh"
plan_stage "stage_09" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_10" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_11" "spark/spark_data_skew.sh"
plan_stage "stage_12" "spark/spark_runtime_ops.sh"

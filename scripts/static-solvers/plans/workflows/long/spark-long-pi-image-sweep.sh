#!/usr/bin/env bash
# Generated from workflows/long/spark-long-pi-image-sweep.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/deploy_spark_pi.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/deploy_spark_pi.sh"
plan_stage "stage_05" "spark/deploy_spark_pi.sh"
plan_stage "stage_06" "spark/deploy_spark_pi.sh"
plan_stage "stage_07" "spark/deploy_spark_pi.sh"
plan_stage "stage_08" "spark/deploy_spark_pi.sh"
plan_stage "stage_09" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_10" "spark/spark_runtime_ops.sh"
plan_stage "stage_11" "spark/spark_multi_tenant.sh"
plan_stage "stage_12" "spark/spark_streaming_autoscale.sh"

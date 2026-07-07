#!/usr/bin/env bash
# Generated from workflows/error-prone/spark-streaming-data-skew-sparkpi-rollback-audit.yaml

plan_stage "stage_01" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_02" "spark/spark_data_skew.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/rollback-rehearsal.sh"
plan_stage "stage_05" "spark/readonly-audit.sh"

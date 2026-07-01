#!/usr/bin/env bash
# Generated from workflows/short/spark-skew-tuning.yaml

plan_stage "stage_01" "spark/spark_data_skew.sh"
plan_stage "stage_02" "spark/spark_data_skew.sh"
plan_stage "stage_03" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_04" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_05" "spark/spark_etl_skew_oom.sh"

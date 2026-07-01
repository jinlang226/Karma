#!/usr/bin/env bash
# Generated from workflows/error-prone/spark-sparkpi-data-skew-change-plan.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/spark_data_skew.sh"
plan_stage "stage_03" "spark/change-plan-only.sh"

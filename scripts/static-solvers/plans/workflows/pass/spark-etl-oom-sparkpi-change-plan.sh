#!/usr/bin/env bash
# Generated from workflows/pass/spark-etl-oom-sparkpi-change-plan.yaml

plan_stage "stage_01" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_02" "spark/deploy_spark_pi.sh"
plan_stage "stage_03" "spark/change-plan-only.sh"

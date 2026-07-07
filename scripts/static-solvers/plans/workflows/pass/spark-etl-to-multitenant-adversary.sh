#!/usr/bin/env bash
# Generated from workflows/pass/spark-etl-to-multitenant-adversary.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_03" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_04" "spark/spark_multi_tenant.sh"
plan_stage "stage_05" "spark/spark_multi_tenant.sh"
plan_stage "stage_06" "spark/spark_runtime_ops.sh"

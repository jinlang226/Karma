#!/usr/bin/env bash
# Generated from workflows/short/spark-runtime-then-multitenant-adversary.yaml

plan_stage "stage_01" "spark/spark_runtime_ops.sh"
plan_stage "stage_02" "spark/spark_runtime_ops.sh"
plan_stage "stage_03" "spark/spark_runtime_ops.sh"
plan_stage "stage_04" "spark/spark_multi_tenant.sh"
plan_stage "stage_05" "spark/spark_multi_tenant.sh"
plan_stage "stage_06" "spark/spark_multi_tenant.sh"

#!/usr/bin/env bash
# Generated from workflows/pass/spark-runtime-ops-data-skew-sparkpi-rollback.yaml

plan_stage "stage_01" "spark/spark_runtime_ops.sh"
plan_stage "stage_02" "spark/spark_data_skew.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/rollback-rehearsal.sh"

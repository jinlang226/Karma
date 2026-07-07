#!/usr/bin/env bash
# Generated from workflows/pass/spark-runtime-ops-sparkpi-audit.yaml

plan_stage "stage_01" "spark/spark_runtime_ops.sh"
plan_stage "stage_02" "spark/deploy_spark_pi.sh"
plan_stage "stage_03" "spark/readonly-audit.sh"

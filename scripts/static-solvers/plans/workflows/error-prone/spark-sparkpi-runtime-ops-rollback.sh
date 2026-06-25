#!/usr/bin/env bash
# Generated from workflows/error-prone/spark-sparkpi-runtime-ops-rollback.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/spark_runtime_ops.sh"
plan_stage "stage_03" "spark/rollback-rehearsal.sh"

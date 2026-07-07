#!/usr/bin/env bash
# Generated from workflows/pass/spark-sparkpi-mid-audit-runtime-ops-change-plan.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/readonly-audit.sh"
plan_stage "stage_03" "spark/spark_runtime_ops.sh"
plan_stage "stage_04" "spark/change-plan-only.sh"

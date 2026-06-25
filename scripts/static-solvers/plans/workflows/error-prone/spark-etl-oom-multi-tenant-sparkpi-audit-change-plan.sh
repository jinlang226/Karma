#!/usr/bin/env bash
# Generated from workflows/error-prone/spark-etl-oom-multi-tenant-sparkpi-audit-change-plan.yaml

plan_stage "stage_01" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_02" "spark/spark_multi_tenant.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/readonly-audit.sh"
plan_stage "stage_05" "spark/change-plan-only.sh"

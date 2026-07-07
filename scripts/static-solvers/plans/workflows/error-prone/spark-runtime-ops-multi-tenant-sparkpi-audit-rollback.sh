#!/usr/bin/env bash
# Generated from workflows/error-prone/spark-runtime-ops-multi-tenant-sparkpi-audit-rollback.yaml

plan_stage "stage_01" "spark/spark_runtime_ops.sh"
plan_stage "stage_02" "spark/spark_multi_tenant.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/readonly-audit.sh"
plan_stage "stage_05" "spark/rollback-rehearsal.sh"

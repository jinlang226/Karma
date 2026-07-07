#!/usr/bin/env bash
# Generated from workflows/error-prone/spark-multi-tenant-sparkpi-change-plan.yaml

plan_stage "stage_01" "spark/spark_multi_tenant.sh"
plan_stage "stage_02" "spark/deploy_spark_pi.sh"
plan_stage "stage_03" "spark/change-plan-only.sh"

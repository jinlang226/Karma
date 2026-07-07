#!/usr/bin/env bash
# Generated from workflows/pass/spark-multi-tenant-data-skew-sparkpi-change-plan.yaml

plan_stage "stage_01" "spark/spark_multi_tenant.sh"
plan_stage "stage_02" "spark/spark_data_skew.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/change-plan-only.sh"

#!/usr/bin/env bash
# Generated from workflows/pass/spark-sparkpi-multi-tenant-audit.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/spark_multi_tenant.sh"
plan_stage "stage_03" "spark/readonly-audit.sh"

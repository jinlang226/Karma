#!/usr/bin/env bash
# Generated from workflows/pass/spark-runtime-ops-streaming-multi-tenant-sparkpi-audit.yaml

plan_stage "stage_01" "spark/spark_runtime_ops.sh"
plan_stage "stage_02" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_03" "spark/spark_multi_tenant.sh"
plan_stage "stage_04" "spark/deploy_spark_pi.sh"
plan_stage "stage_05" "spark/readonly-audit.sh"

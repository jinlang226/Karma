#!/usr/bin/env bash
# Generated from workflows/error-prone/spark-multi-tenant-streaming-sparkpi-rollback.yaml

plan_stage "stage_01" "spark/spark_multi_tenant.sh"
plan_stage "stage_02" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/rollback-rehearsal.sh"

#!/usr/bin/env bash
# Generated from workflows/pass/spark-runtime-ops-streaming-sparkpi-rollback-change-plan.yaml

plan_stage "stage_01" "spark/spark_runtime_ops.sh"
plan_stage "stage_02" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/rollback-rehearsal.sh"
plan_stage "stage_05" "spark/change-plan-only.sh"

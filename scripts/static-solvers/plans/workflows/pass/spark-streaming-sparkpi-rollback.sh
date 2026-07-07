#!/usr/bin/env bash
# Generated from workflows/pass/spark-streaming-sparkpi-rollback.yaml

plan_stage "stage_01" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_02" "spark/deploy_spark_pi.sh"
plan_stage "stage_03" "spark/rollback-rehearsal.sh"

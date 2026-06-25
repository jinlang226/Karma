#!/usr/bin/env bash
# Generated from workflows/short/platform-analytics-stack-adversary.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/job_submission.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/spark_streaming_autoscale.sh"

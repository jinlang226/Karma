#!/usr/bin/env bash
# Generated from workflows/short/spark-streaming-sweep-adversary.yaml

plan_stage "stage_01" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_02" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_03" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_04" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_05" "spark/spark_streaming_autoscale.sh"

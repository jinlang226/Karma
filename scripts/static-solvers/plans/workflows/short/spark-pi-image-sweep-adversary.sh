#!/usr/bin/env bash
# Generated from workflows/short/spark-pi-image-sweep-adversary.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/deploy_spark_pi.sh"
plan_stage "stage_03" "spark/deploy_spark_pi.sh"
plan_stage "stage_04" "spark/deploy_spark_pi.sh"

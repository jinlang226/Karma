#!/usr/bin/env bash
# Generated from workflows/short/platform-search-etl-fault-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "spark/deploy_spark_pi.sh"
plan_stage "stage_03" "spark/spark_data_skew.sh"
plan_stage "stage_04" "elasticsearch/seed-hosts-repair.sh"

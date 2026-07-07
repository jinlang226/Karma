#!/usr/bin/env bash
# Generated from workflows/short/platform-lakehouse-etl.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "spark/deploy_spark_pi.sh"
plan_stage "stage_03" "spark/spark_etl_skew_oom.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"

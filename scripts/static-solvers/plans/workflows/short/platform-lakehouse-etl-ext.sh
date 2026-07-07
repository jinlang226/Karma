#!/usr/bin/env bash
# Generated from workflows/short/platform-lakehouse-etl-ext.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/spark_data_skew.sh"
plan_stage "stage_03" "spark/spark_runtime_ops.sh"
plan_stage "stage_04" "spark/spark_multi_tenant.sh"
plan_stage "stage_05" "spark/spark_streaming_autoscale.sh"
plan_stage "stage_06" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_07" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_08" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_09" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_10" "elasticsearch/stack-monitoring-sidecars.sh"

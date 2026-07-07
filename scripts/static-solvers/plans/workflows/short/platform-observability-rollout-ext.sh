#!/usr/bin/env bash
# Generated from workflows/short/platform-observability-rollout-ext.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_03" "mongodb/deploy.sh"
plan_stage "stage_04" "mongodb/monitoring-integration.sh"
plan_stage "stage_05" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_06" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_07" "ray/deploy_cluster.sh"
plan_stage "stage_08" "ray/dashboard_exposure.sh"
plan_stage "stage_09" "spark/deploy_spark_pi.sh"
plan_stage "stage_10" "spark/spark_runtime_ops.sh"

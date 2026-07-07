#!/usr/bin/env bash
# Generated from workflows/long/platform-long-analytics-stack.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/dashboard_exposure.sh"
plan_stage "stage_03" "ray/job_submission.sh"
plan_stage "stage_04" "ray/scale_workers.sh"
plan_stage "stage_05" "spark/deploy_spark_pi.sh"
plan_stage "stage_06" "spark/spark_data_skew.sh"
plan_stage "stage_07" "spark/spark_runtime_ops.sh"
plan_stage "stage_08" "spark/spark_multi_tenant.sh"
plan_stage "stage_09" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_10" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_11" "elasticsearch/scale-up-new-nodeset.sh"

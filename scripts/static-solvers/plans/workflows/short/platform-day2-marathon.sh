#!/usr/bin/env bash
# Generated from workflows/short/platform-day2-marathon.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/generate-cert.sh"
plan_stage "stage_04" "mongodb/deploy.sh"
plan_stage "stage_05" "mongodb/initialize.sh"
plan_stage "stage_06" "mongodb/user-management.sh"
plan_stage "stage_07" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_08" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_09" "spark/deploy_spark_pi.sh"
plan_stage "stage_10" "spark/spark_data_skew.sh"

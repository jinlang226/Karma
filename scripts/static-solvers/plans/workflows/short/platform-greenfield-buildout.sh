#!/usr/bin/env bash
# Generated from workflows/short/platform-greenfield-buildout.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "mongodb/deploy.sh"
plan_stage "stage_04" "mongodb/initialize.sh"
plan_stage "stage_05" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_06" "ray/deploy_cluster.sh"
plan_stage "stage_07" "ray/job_submission.sh"
plan_stage "stage_08" "spark/deploy_spark_pi.sh"
plan_stage "stage_09" "nginx-ingress/create_ingress.sh"
plan_stage "stage_10" "nginx-ingress/rate_limit_ingress_easy.sh"

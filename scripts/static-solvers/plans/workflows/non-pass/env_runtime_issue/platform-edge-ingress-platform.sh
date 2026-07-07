#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/platform-edge-ingress-platform.yaml

plan_stage "stage_01" "nginx-ingress/create_ingress.sh"
plan_stage "stage_02" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_03" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_04" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_05" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_06" "mongodb/deploy.sh"
plan_stage "stage_07" "mongodb/initialize.sh"
plan_stage "stage_08" "mongodb/user-management.sh"
plan_stage "stage_09" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_10" "elasticsearch/file-realm-user-roles-merge.sh"

#!/usr/bin/env bash
# Generated from workflows/short/platform-search-behind-ingress.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_03" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_05" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_06" "nginx-ingress/create_ingress.sh"
plan_stage "stage_07" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_08" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_09" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_10" "nginx-ingress/otel_log_format.sh"

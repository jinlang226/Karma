#!/usr/bin/env bash
# Generated from workflows/long/platform-long-fronted-store.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/user-management.sh"
plan_stage "stage_04" "mongodb/tls-setup.sh"
plan_stage "stage_05" "mongodb/replica-scaling.sh"
plan_stage "stage_06" "mongodb/monitoring-integration.sh"
plan_stage "stage_07" "nginx-ingress/create_ingress.sh"
plan_stage "stage_08" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_09" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_10" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_11" "nginx-ingress/otel_log_format.sh"

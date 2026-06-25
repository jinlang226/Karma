#!/usr/bin/env bash
# Generated from workflows/long/nginx-ingress-long-08-otel-configmap-fault.yaml

plan_stage "stage_01" "nginx-ingress/create_ingress.sh"
plan_stage "stage_02" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_03" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_04" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_05" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_06" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_07" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_08" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_09" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_10" "nginx-ingress/class_only_upgrade.sh"
plan_stage "stage_11" "nginx-ingress/otel_log_format.sh"

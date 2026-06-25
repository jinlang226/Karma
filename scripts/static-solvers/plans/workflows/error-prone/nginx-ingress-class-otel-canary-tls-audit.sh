#!/usr/bin/env bash
# Generated from workflows/error-prone/nginx-ingress-class-otel-canary-tls-audit.yaml

plan_stage "stage_01" "nginx-ingress/class_only_upgrade.sh"
plan_stage "stage_02" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_03" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_04" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_05" "nginx-ingress/readonly-audit.sh"

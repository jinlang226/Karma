#!/usr/bin/env bash
# Generated from workflows/pass/nginx-ingress-canary-otel-tls-audit.yaml

plan_stage "stage_01" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_02" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_03" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_04" "nginx-ingress/readonly-audit.sh"

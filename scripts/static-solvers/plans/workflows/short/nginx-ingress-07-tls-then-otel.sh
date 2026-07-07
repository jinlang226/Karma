#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-07-tls-then-otel.yaml

plan_stage "stage_01" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_02" "nginx-ingress/otel_log_format.sh"

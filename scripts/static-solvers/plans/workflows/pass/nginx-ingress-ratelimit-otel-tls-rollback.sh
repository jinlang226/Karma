#!/usr/bin/env bash
# Generated from workflows/pass/nginx-ingress-ratelimit-otel-tls-rollback.yaml

plan_stage "stage_01" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_02" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_03" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_04" "nginx-ingress/rollback-rehearsal.sh"

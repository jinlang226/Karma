#!/usr/bin/env bash
# Generated from workflows/pass/nginx-ingress-ratelimit-canary-tls-otel-change-plan-rollback.yaml

plan_stage "stage_01" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_02" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_03" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_04" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_05" "nginx-ingress/change-plan-only.sh"
plan_stage "stage_06" "nginx-ingress/rollback-rehearsal.sh"

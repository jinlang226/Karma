#!/usr/bin/env bash
# Generated from workflows/error-prone/nginx-ingress-canary-tls-otel-rollback-change-plan.yaml

plan_stage "stage_01" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_02" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_03" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_04" "nginx-ingress/rollback-rehearsal.sh"
plan_stage "stage_05" "nginx-ingress/change-plan-only.sh"

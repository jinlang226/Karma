#!/usr/bin/env bash
# Generated from workflows/pass/nginx-ingress-15-e2e-five-stage.yaml

plan_stage "stage_01" "nginx-ingress/create_ingress.sh"
plan_stage "stage_02" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_03" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_04" "nginx-ingress/rate_limit_replica_hard.sh"
plan_stage "stage_05" "nginx-ingress/otel_log_format.sh"

#!/usr/bin/env bash
# Generated from workflows/pass/nginx-ingress-tls-ratelimit-audit.yaml

plan_stage "stage_01" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_02" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_03" "nginx-ingress/readonly-audit.sh"

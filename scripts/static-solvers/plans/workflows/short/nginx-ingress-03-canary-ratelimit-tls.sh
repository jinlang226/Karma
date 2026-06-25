#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-03-canary-ratelimit-tls.yaml

plan_stage "stage_01" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_02" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_03" "nginx-ingress/renew_tls_secret.sh"

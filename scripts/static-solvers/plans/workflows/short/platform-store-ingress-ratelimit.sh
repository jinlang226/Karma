#!/usr/bin/env bash
# Generated from workflows/short/platform-store-ingress-ratelimit.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "nginx-ingress/create_ingress.sh"
plan_stage "stage_03" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_04" "nginx-ingress/renew_tls_secret.sh"

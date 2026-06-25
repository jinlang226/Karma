#!/usr/bin/env bash
# Generated from workflows/error-prone/nginx-ingress-expose-tls-canary-audit.yaml

plan_stage "stage_01" "nginx-ingress/create_ingress.sh"
plan_stage "stage_02" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_03" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_04" "nginx-ingress/readonly-audit.sh"

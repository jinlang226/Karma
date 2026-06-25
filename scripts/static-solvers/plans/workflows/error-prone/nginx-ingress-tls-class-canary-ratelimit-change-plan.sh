#!/usr/bin/env bash
# Generated from workflows/error-prone/nginx-ingress-tls-class-canary-ratelimit-change-plan.yaml

plan_stage "stage_01" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_02" "nginx-ingress/class_only_upgrade.sh"
plan_stage "stage_03" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_04" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_05" "nginx-ingress/change-plan-only.sh"

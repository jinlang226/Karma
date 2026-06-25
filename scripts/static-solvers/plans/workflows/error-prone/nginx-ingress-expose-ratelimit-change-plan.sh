#!/usr/bin/env bash
# Generated from workflows/error-prone/nginx-ingress-expose-ratelimit-change-plan.yaml

plan_stage "stage_01" "nginx-ingress/create_ingress.sh"
plan_stage "stage_02" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_03" "nginx-ingress/change-plan-only.sh"

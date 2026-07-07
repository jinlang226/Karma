#!/usr/bin/env bash
# Generated from workflows/pass/nginx-ingress-ratelimit-tighten-canary-change-plan.yaml

plan_stage "stage_01" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_02" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_03" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_04" "nginx-ingress/change-plan-only.sh"

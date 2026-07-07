#!/usr/bin/env bash
# Generated from workflows/pass/nginx-ingress-ratelimit-canary-rollback.yaml

plan_stage "stage_01" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_02" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_03" "nginx-ingress/rollback-rehearsal.sh"

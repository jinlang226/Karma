#!/usr/bin/env bash
# Generated from workflows/pass/nginx-ingress-01-create-then-ratelimit.yaml

plan_stage "stage_01" "nginx-ingress/create_ingress.sh"
plan_stage "stage_02" "nginx-ingress/rate_limit_ingress_easy.sh"

#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-05-ratelimit-sweep-easy.yaml

plan_stage "stage_01" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_02" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_03" "nginx-ingress/rate_limit_ingress_easy.sh"

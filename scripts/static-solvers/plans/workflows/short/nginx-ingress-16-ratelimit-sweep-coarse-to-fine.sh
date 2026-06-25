#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-16-ratelimit-sweep-coarse-to-fine.yaml

plan_stage "stage_01" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_02" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_03" "nginx-ingress/rate_limit_ingress_easy.sh"

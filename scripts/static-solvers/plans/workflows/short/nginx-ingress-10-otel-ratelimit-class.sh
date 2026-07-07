#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-10-otel-ratelimit-class.yaml

plan_stage "stage_01" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_02" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_03" "nginx-ingress/class_only_upgrade.sh"

#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-09-full-lifecycle-adversary.yaml

plan_stage "stage_01" "nginx-ingress/create_ingress.sh"
plan_stage "stage_02" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_03" "nginx-ingress/rate_limit_ingress_easy.sh"
plan_stage "stage_04" "nginx-ingress/class_only_upgrade.sh"

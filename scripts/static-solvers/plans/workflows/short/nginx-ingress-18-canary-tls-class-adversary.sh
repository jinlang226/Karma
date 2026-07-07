#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-18-canary-tls-class-adversary.yaml

plan_stage "stage_01" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_02" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_03" "nginx-ingress/class_only_upgrade.sh"

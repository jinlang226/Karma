#!/usr/bin/env bash
# Generated from workflows/pass/nginx-ingress-11-tls-canary-ratelimit-hard.yaml

plan_stage "stage_01" "nginx-ingress/renew_tls_secret.sh"
plan_stage "stage_02" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_03" "nginx-ingress/rate_limit_replica_hard.sh"

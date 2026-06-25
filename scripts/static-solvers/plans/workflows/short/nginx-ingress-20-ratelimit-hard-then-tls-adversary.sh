#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-20-ratelimit-hard-then-tls-adversary.yaml

plan_stage "stage_01" "nginx-ingress/rate_limit_replica_hard.sh"
plan_stage "stage_02" "nginx-ingress/renew_tls_secret.sh"

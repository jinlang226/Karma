#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-06-ratelimit-sweep-hard-adversary.yaml

plan_stage "stage_01" "nginx-ingress/rate_limit_replica_hard.sh"
plan_stage "stage_02" "nginx-ingress/rate_limit_replica_hard.sh"
plan_stage "stage_03" "nginx-ingress/rate_limit_replica_hard.sh"

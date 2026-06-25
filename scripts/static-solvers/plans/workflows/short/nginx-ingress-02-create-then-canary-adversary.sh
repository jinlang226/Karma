#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-02-create-then-canary-adversary.yaml

plan_stage "stage_01" "nginx-ingress/create_ingress.sh"
plan_stage "stage_02" "nginx-ingress/ingress_canary.sh"

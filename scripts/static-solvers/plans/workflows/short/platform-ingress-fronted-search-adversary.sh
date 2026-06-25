#!/usr/bin/env bash
# Generated from workflows/short/platform-ingress-fronted-search-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_03" "nginx-ingress/create_ingress.sh"
plan_stage "stage_04" "nginx-ingress/rate_limit_ingress_easy.sh"

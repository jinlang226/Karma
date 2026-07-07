#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-service-and-network-repair-adversary.yaml

plan_stage "stage_01" "elasticsearch/internal-http-service-drift.sh"
plan_stage "stage_02" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_03" "elasticsearch/secure-http-ingress.sh"

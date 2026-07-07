#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-networking-repair-ext.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_03" "elasticsearch/internal-http-service-drift.sh"
plan_stage "stage_04" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_05" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_06" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_07" "elasticsearch/secure-http-ingress.sh"

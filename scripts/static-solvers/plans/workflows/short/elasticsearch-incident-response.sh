#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-incident-response.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/internal-http-service-drift.sh"
plan_stage "stage_03" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_04" "elasticsearch/seed-hosts-repair.sh"

#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-network-incident-recovery.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/transport-additional-ca-trust.sh"
plan_stage "stage_03" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_04" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_05" "elasticsearch/transport-additional-ca-trust.sh"
plan_stage "stage_06" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_07" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_08" "elasticsearch/transport-additional-ca-trust.sh"

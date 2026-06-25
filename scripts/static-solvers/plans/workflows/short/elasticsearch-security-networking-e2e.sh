#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-security-networking-e2e.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_04" "elasticsearch/transport-additional-ca-trust.sh"
plan_stage "stage_05" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_06" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_07" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_08" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_09" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_10" "elasticsearch/seed-hosts-repair.sh"

#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-security-with-transport-block-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/transport-additional-ca-trust.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_04" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_05" "elasticsearch/secure-http-ingress.sh"

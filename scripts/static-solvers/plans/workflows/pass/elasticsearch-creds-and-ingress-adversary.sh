#!/usr/bin/env bash
# Generated from workflows/pass/elasticsearch-creds-and-ingress-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_04" "elasticsearch/secure-http-ingress.sh"

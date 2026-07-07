#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/elasticsearch-creds-and-ingress.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_04" "elasticsearch/secure-http-ingress.sh"

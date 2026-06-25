#!/usr/bin/env bash
# Generated from workflows/error-prone/elasticsearch-ingress-snapshot-certs-change-plan.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_03" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_04" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_05" "elasticsearch/change-plan-only.sh"

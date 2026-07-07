#!/usr/bin/env bash
# Generated from workflows/pass/elasticsearch-password-ingress-audit-change-plan.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_03" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_04" "elasticsearch/readonly-audit.sh"
plan_stage "stage_05" "elasticsearch/change-plan-only.sh"

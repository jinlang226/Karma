#!/usr/bin/env bash
# Generated from workflows/error-prone/elasticsearch-certs-password-users-audit-change-plan.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_03" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_04" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_05" "elasticsearch/readonly-audit.sh"
plan_stage "stage_06" "elasticsearch/change-plan-only.sh"

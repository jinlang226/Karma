#!/usr/bin/env bash
# Generated from workflows/error-prone/elasticsearch-snapshot-custom-users-certs-audit.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_03" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_04" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_05" "elasticsearch/readonly-audit.sh"

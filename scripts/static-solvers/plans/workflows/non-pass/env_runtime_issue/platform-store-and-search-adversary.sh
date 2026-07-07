#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/platform-store-and-search-adversary.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/user-management.sh"
plan_stage "stage_03" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_04" "elasticsearch/file-realm-user-roles-merge.sh"

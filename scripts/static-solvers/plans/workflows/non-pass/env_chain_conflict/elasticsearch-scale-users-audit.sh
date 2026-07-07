#!/usr/bin/env bash
# Generated from workflows/non-pass/env_chain_conflict/elasticsearch-scale-users-audit.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_03" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_04" "elasticsearch/readonly-audit.sh"

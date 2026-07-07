#!/usr/bin/env bash
# Generated from workflows/error-prone/elasticsearch-scale-snapshot-audit.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_03" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_04" "elasticsearch/readonly-audit.sh"

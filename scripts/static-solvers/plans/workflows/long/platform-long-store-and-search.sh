#!/usr/bin/env bash
# Generated from workflows/long/platform-long-store-and-search.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/user-management.sh"
plan_stage "stage_04" "mongodb/tls-setup.sh"
plan_stage "stage_05" "mongodb/replica-scaling.sh"
plan_stage "stage_06" "mongodb/monitoring-integration.sh"
plan_stage "stage_07" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_08" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_09" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_10" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_11" "elasticsearch/stack-monitoring-sidecars.sh"

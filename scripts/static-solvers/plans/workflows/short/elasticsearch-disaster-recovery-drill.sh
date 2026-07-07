#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-disaster-recovery-drill.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_03" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_04" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_05" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_06" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_07" "elasticsearch/safe-downscale-with-shard-migration.sh"
plan_stage "stage_08" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_09" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_10" "elasticsearch/stack-monitoring-sidecars.sh"

#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-decommission-reprovision.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_03" "elasticsearch/safe-downscale-with-shard-migration.sh"
plan_stage "stage_04" "elasticsearch/master-downscale-voting-exclusions.sh"
plan_stage "stage_05" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_06" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_07" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_08" "elasticsearch/stack-monitoring-sidecars.sh"

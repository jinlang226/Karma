#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-dr-shard-upgrade.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_03" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_05" "elasticsearch/full-restart-upgrade-ha.sh"
plan_stage "stage_06" "elasticsearch/full-restart-upgrade-ha-hard.sh"
plan_stage "stage_07" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_08" "elasticsearch/stack-monitoring-sidecars.sh"

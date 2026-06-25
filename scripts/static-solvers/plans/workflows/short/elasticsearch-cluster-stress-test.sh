#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-cluster-stress-test.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_03" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_05" "elasticsearch/transport-additional-ca-trust.sh"
plan_stage "stage_06" "elasticsearch/safe-downscale-with-shard-migration.sh"
plan_stage "stage_07" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_08" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_09" "elasticsearch/stack-monitoring-sidecars.sh"

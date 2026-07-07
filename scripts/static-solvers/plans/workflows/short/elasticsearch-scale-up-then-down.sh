#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-scale-up-then-down.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_03" "elasticsearch/safe-downscale-with-shard-migration.sh"

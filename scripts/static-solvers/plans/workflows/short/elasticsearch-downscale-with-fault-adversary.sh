#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-downscale-with-fault-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/master-downscale-voting-exclusions.sh"
plan_stage "stage_03" "elasticsearch/safe-downscale-with-shard-migration.sh"

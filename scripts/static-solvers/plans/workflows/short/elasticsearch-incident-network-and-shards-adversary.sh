#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-incident-network-and-shards-adversary.yaml

plan_stage "stage_01" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_02" "elasticsearch/internal-http-service-drift.sh"
plan_stage "stage_03" "elasticsearch/safe-downscale-with-shard-migration.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"

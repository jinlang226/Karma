#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-snapshot-secret-recovery.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_03" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_04" "elasticsearch/safe-downscale-with-shard-migration.sh"

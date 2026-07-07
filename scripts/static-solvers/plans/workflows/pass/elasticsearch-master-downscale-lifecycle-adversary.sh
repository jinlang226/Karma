#!/usr/bin/env bash
# Generated from workflows/pass/elasticsearch-master-downscale-lifecycle-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_03" "elasticsearch/master-downscale-voting-exclusions.sh"

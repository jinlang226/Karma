#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-security-networking-snapshot-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_03" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"

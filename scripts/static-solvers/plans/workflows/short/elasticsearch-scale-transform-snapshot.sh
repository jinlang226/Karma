#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-scale-transform-snapshot.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_03" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"

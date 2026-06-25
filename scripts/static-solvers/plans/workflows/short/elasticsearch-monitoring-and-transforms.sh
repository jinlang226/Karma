#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-monitoring-and-transforms.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_03" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"

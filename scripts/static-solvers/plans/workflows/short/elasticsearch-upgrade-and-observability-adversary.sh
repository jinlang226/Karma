#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-upgrade-and-observability-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/full-restart-upgrade-ha.sh"
plan_stage "stage_03" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_05" "elasticsearch/transform-job-recovery.sh"

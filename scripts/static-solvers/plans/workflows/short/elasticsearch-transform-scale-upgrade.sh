#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-transform-scale-upgrade.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_03" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_04" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_05" "elasticsearch/full-restart-upgrade-ha.sh"
plan_stage "stage_06" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_07" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_08" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_09" "elasticsearch/stack-monitoring-sidecars.sh"

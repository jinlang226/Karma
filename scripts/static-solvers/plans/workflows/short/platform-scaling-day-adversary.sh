#!/usr/bin/env bash
# Generated from workflows/short/platform-scaling-day-adversary.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/replica-scaling.sh"
plan_stage "stage_03" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_04" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_05" "ray/deploy_cluster.sh"
plan_stage "stage_06" "ray/scale_workers.sh"

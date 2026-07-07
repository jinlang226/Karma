#!/usr/bin/env bash
# Generated from workflows/short/platform-observability-rollout.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_03" "mongodb/deploy.sh"
plan_stage "stage_04" "mongodb/monitoring-integration.sh"
plan_stage "stage_05" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_06" "elasticsearch/stack-monitoring-sidecars.sh"

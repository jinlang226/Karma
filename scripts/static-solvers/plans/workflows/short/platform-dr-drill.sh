#!/usr/bin/env bash
# Generated from workflows/short/platform-dr-drill.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_04" "mongodb/deploy.sh"
plan_stage "stage_05" "mongodb/initialize.sh"
plan_stage "stage_06" "mongodb/monitoring-integration.sh"
plan_stage "stage_07" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_08" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_09" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_10" "elasticsearch/seed-hosts-repair.sh"

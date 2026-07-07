#!/usr/bin/env bash
# Generated from workflows/short/platform-incident-response-drill.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/health-check-recovery.sh"
plan_stage "stage_04" "cockroachdb/deploy.sh"
plan_stage "stage_05" "cockroachdb/initialize.sh"
plan_stage "stage_06" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_07" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_08" "ray/deploy_cluster.sh"
plan_stage "stage_09" "ray/worker_recovery.sh"

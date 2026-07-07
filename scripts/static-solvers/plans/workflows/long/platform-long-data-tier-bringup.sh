#!/usr/bin/env bash
# Generated from workflows/long/platform-long-data-tier-bringup.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "cockroachdb/generate-cert.sh"
plan_stage "stage_04" "cockroachdb/cluster-settings.sh"
plan_stage "stage_05" "cockroachdb/zone-config.sh"
plan_stage "stage_06" "cockroachdb/monitoring-integration.sh"
plan_stage "stage_07" "mongodb/deploy.sh"
plan_stage "stage_08" "mongodb/initialize.sh"
plan_stage "stage_09" "mongodb/user-management.sh"
plan_stage "stage_10" "mongodb/tls-setup.sh"
plan_stage "stage_11" "mongodb/replica-scaling.sh"
plan_stage "stage_12" "mongodb/monitoring-integration.sh"

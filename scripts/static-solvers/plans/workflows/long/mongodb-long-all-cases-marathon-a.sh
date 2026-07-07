#!/usr/bin/env bash
# Generated from workflows/long/mongodb-long-all-cases-marathon-a.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/arbiters.sh"
plan_stage "stage_04" "mongodb/user-management.sh"
plan_stage "stage_05" "mongodb/custom-roles.sh"
plan_stage "stage_06" "mongodb/tls-setup.sh"
plan_stage "stage_07" "mongodb/certificate-rotation.sh"
plan_stage "stage_08" "mongodb/external-access-horizons.sh"
plan_stage "stage_09" "mongodb/replica-scaling.sh"
plan_stage "stage_10" "mongodb/statefulset-customization.sh"
plan_stage "stage_11" "mongodb/health-check-recovery.sh"
plan_stage "stage_12" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_13" "mongodb/mongod-config-update.sh"
plan_stage "stage_14" "mongodb/monitoring-integration.sh"
plan_stage "stage_15" "mongodb/password-rotation.sh"

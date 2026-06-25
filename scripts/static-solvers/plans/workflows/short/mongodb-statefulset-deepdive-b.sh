#!/usr/bin/env bash
# Generated from workflows/short/mongodb-statefulset-deepdive-b.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/statefulset-customization.sh"
plan_stage "stage_04" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_05" "mongodb/health-check-recovery.sh"
plan_stage "stage_06" "mongodb/custom-roles.sh"
plan_stage "stage_07" "mongodb/replica-scaling.sh"
plan_stage "stage_08" "mongodb/mongod-config-update.sh"
plan_stage "stage_09" "mongodb/user-management.sh"
plan_stage "stage_10" "mongodb/monitoring-integration.sh"

#!/usr/bin/env bash
# Generated from workflows/short/mongodb-observability-rollout-b.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/monitoring-integration.sh"
plan_stage "stage_04" "mongodb/mongod-config-update.sh"
plan_stage "stage_05" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_06" "mongodb/mongod-config-update.sh"
plan_stage "stage_07" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_08" "mongodb/password-rotation.sh"
plan_stage "stage_09" "mongodb/user-management.sh"
plan_stage "stage_10" "mongodb/custom-roles.sh"

#!/usr/bin/env bash
# Generated from workflows/short/mongodb-full-lifecycle-b-ext.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/tls-setup.sh"
plan_stage "stage_04" "mongodb/certificate-rotation.sh"
plan_stage "stage_05" "mongodb/custom-roles.sh"
plan_stage "stage_06" "mongodb/user-management.sh"
plan_stage "stage_07" "mongodb/replica-scaling.sh"
plan_stage "stage_08" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_09" "mongodb/monitoring-integration.sh"
plan_stage "stage_10" "mongodb/mongod-config-update.sh"

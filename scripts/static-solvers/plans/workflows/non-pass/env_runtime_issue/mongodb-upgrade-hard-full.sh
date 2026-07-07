#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/mongodb-upgrade-hard-full.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/replica-scaling.sh"
plan_stage "stage_04" "mongodb/version-upgrade-hard.sh"
plan_stage "stage_05" "mongodb/tls-setup.sh"
plan_stage "stage_06" "mongodb/certificate-rotation.sh"
plan_stage "stage_07" "mongodb/custom-roles.sh"
plan_stage "stage_08" "mongodb/monitoring-integration.sh"
plan_stage "stage_09" "mongodb/mongod-config-update.sh"
plan_stage "stage_10" "mongodb/mongod-config-update.sh"

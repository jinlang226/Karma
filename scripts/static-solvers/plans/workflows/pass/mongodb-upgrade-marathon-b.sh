#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-upgrade-marathon-b.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/replica-scaling.sh"
plan_stage "stage_04" "mongodb/mongod-config-update.sh"
plan_stage "stage_05" "mongodb/mongod-config-update.sh"
plan_stage "stage_06" "mongodb/version-upgrade.sh"
plan_stage "stage_07" "mongodb/tls-setup.sh"
plan_stage "stage_08" "mongodb/certificate-rotation.sh"
plan_stage "stage_09" "mongodb/monitoring-integration.sh"
plan_stage "stage_10" "mongodb/readiness-probe-tuning.sh"

#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-arbiter-lifecycle-a.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/arbiters.sh"
plan_stage "stage_04" "mongodb/replica-scaling.sh"
plan_stage "stage_05" "mongodb/mongod-config-update.sh"
plan_stage "stage_06" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_07" "mongodb/mongod-config-update.sh"
plan_stage "stage_08" "mongodb/tls-setup.sh"
plan_stage "stage_09" "mongodb/monitoring-integration.sh"
plan_stage "stage_10" "mongodb/certificate-rotation.sh"

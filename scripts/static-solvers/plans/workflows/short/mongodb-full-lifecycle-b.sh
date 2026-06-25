#!/usr/bin/env bash
# Generated from workflows/short/mongodb-full-lifecycle-b.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/password-rotation.sh"
plan_stage "stage_03" "mongodb/replica-scaling.sh"
plan_stage "stage_04" "mongodb/mongod-config-update.sh"
plan_stage "stage_05" "mongodb/monitoring-integration.sh"

#!/usr/bin/env bash
# Generated from workflows/short/mongodb-scaling-campaign-b.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/replica-scaling.sh"
plan_stage "stage_04" "mongodb/mongod-config-update.sh"
plan_stage "stage_05" "mongodb/replica-scaling.sh"
plan_stage "stage_06" "mongodb/arbiters.sh"
plan_stage "stage_07" "mongodb/replica-scaling.sh"
plan_stage "stage_08" "mongodb/replica-scaling.sh"
plan_stage "stage_09" "mongodb/monitoring-integration.sh"
plan_stage "stage_10" "mongodb/password-rotation.sh"

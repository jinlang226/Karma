#!/usr/bin/env bash
# Generated from workflows/long/mongodb-long-day2-marathon-a.yaml

plan_stage "stage_01" "mongodb/replica-scaling.sh"
plan_stage "stage_02" "mongodb/replica-scaling.sh"
plan_stage "stage_03" "mongodb/mongod-config-update.sh"
plan_stage "stage_04" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_05" "mongodb/replica-scaling.sh"
plan_stage "stage_06" "mongodb/mongod-config-update.sh"
plan_stage "stage_07" "mongodb/replica-scaling.sh"
plan_stage "stage_08" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_09" "mongodb/mongod-config-update.sh"
plan_stage "stage_10" "mongodb/password-rotation.sh"
plan_stage "stage_11" "mongodb/monitoring-integration.sh"

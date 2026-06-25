#!/usr/bin/env bash
# Generated from workflows/error-prone/mongodb-snappy-users-scale5-roles-change-plan.yaml

plan_stage "stage_01" "mongodb/mongod-config-update.sh"
plan_stage "stage_02" "mongodb/user-management.sh"
plan_stage "stage_03" "mongodb/replica-scaling.sh"
plan_stage "stage_04" "mongodb/custom-roles.sh"
plan_stage "stage_05" "mongodb/change-plan-only.sh"

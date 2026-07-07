#!/usr/bin/env bash
# Generated from workflows/error-prone/mongodb-roles-snappy-rotate-change-plan.yaml

plan_stage "stage_01" "mongodb/custom-roles.sh"
plan_stage "stage_02" "mongodb/mongod-config-update.sh"
plan_stage "stage_03" "mongodb/password-rotation.sh"
plan_stage "stage_04" "mongodb/change-plan-only.sh"

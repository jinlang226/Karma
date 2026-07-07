#!/usr/bin/env bash
# Generated from workflows/error-prone/mongodb-users-roles-zlib-rotate-audit.yaml

plan_stage "stage_01" "mongodb/user-management.sh"
plan_stage "stage_02" "mongodb/custom-roles.sh"
plan_stage "stage_03" "mongodb/mongod-config-update.sh"
plan_stage "stage_04" "mongodb/password-rotation.sh"
plan_stage "stage_05" "mongodb/readonly-audit.sh"

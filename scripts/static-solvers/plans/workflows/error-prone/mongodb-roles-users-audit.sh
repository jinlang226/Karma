#!/usr/bin/env bash
# Generated from workflows/error-prone/mongodb-roles-users-audit.yaml

plan_stage "stage_01" "mongodb/custom-roles.sh"
plan_stage "stage_02" "mongodb/user-management.sh"
plan_stage "stage_03" "mongodb/readonly-audit.sh"

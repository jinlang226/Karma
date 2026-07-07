#!/usr/bin/env bash
# Generated from workflows/error-prone/mongodb-scale5-snappy-roles-rotate-audit.yaml

plan_stage "stage_01" "mongodb/replica-scaling.sh"
plan_stage "stage_02" "mongodb/mongod-config-update.sh"
plan_stage "stage_03" "mongodb/custom-roles.sh"
plan_stage "stage_04" "mongodb/password-rotation.sh"
plan_stage "stage_05" "mongodb/readonly-audit.sh"

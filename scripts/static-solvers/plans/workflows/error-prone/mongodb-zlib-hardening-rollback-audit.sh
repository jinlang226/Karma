#!/usr/bin/env bash
# Generated from workflows/error-prone/mongodb-zlib-hardening-rollback-audit.yaml

plan_stage "stage_01" "mongodb/mongod-config-update.sh"
plan_stage "stage_02" "mongodb/replica-scaling.sh"
plan_stage "stage_03" "mongodb/custom-roles.sh"
plan_stage "stage_04" "mongodb/user-management.sh"
plan_stage "stage_05" "mongodb/password-rotation.sh"
plan_stage "stage_06" "mongodb/rollback-rehearsal.sh"
plan_stage "stage_07" "mongodb/readonly-audit.sh"

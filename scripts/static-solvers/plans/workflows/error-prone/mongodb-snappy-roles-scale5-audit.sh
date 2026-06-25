#!/usr/bin/env bash
# Generated from workflows/error-prone/mongodb-snappy-roles-scale5-audit.yaml

plan_stage "stage_01" "mongodb/mongod-config-update.sh"
plan_stage "stage_02" "mongodb/custom-roles.sh"
plan_stage "stage_03" "mongodb/replica-scaling.sh"
plan_stage "stage_04" "mongodb/readonly-audit.sh"

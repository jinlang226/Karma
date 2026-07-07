#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-users-scale7-snappy-audit.yaml

plan_stage "stage_01" "mongodb/user-management.sh"
plan_stage "stage_02" "mongodb/replica-scaling.sh"
plan_stage "stage_03" "mongodb/mongod-config-update.sh"
plan_stage "stage_04" "mongodb/readonly-audit.sh"

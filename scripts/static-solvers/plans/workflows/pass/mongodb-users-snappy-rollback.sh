#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-users-snappy-rollback.yaml

plan_stage "stage_01" "mongodb/user-management.sh"
plan_stage "stage_02" "mongodb/mongod-config-update.sh"
plan_stage "stage_03" "mongodb/rollback-rehearsal.sh"

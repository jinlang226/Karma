#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-snappy-roles-rollback.yaml

plan_stage "stage_01" "mongodb/mongod-config-update.sh"
plan_stage "stage_02" "mongodb/custom-roles.sh"
plan_stage "stage_03" "mongodb/rollback-rehearsal.sh"

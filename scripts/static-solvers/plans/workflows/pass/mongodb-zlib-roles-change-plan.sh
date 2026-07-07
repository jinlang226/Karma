#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-zlib-roles-change-plan.yaml

plan_stage "stage_01" "mongodb/mongod-config-update.sh"
plan_stage "stage_02" "mongodb/custom-roles.sh"
plan_stage "stage_03" "mongodb/change-plan-only.sh"

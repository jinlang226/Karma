#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/mongodb-deploy-arbiters-roles.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/arbiters.sh"
plan_stage "stage_03" "mongodb/custom-roles.sh"
plan_stage "stage_04" "mongodb/user-management.sh"

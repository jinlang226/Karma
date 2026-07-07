#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-roles-scale5-audit.yaml

plan_stage "stage_01" "mongodb/custom-roles.sh"
plan_stage "stage_02" "mongodb/replica-scaling.sh"
plan_stage "stage_03" "mongodb/readonly-audit.sh"

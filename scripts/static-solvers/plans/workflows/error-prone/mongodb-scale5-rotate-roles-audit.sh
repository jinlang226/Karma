#!/usr/bin/env bash
# Generated from workflows/error-prone/mongodb-scale5-rotate-roles-audit.yaml

plan_stage "stage_01" "mongodb/replica-scaling.sh"
plan_stage "stage_02" "mongodb/password-rotation.sh"
plan_stage "stage_03" "mongodb/custom-roles.sh"
plan_stage "stage_04" "mongodb/readonly-audit.sh"

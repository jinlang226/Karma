#!/usr/bin/env bash
# Generated from workflows/error-prone/mongodb-scale5-rotate-users-change-plan.yaml

plan_stage "stage_01" "mongodb/replica-scaling.sh"
plan_stage "stage_02" "mongodb/password-rotation.sh"
plan_stage "stage_03" "mongodb/user-management.sh"
plan_stage "stage_04" "mongodb/change-plan-only.sh"

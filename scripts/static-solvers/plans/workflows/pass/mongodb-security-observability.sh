#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-security-observability.yaml

plan_stage "stage_01" "mongodb/user-management.sh"
plan_stage "stage_02" "mongodb/password-rotation.sh"
plan_stage "stage_03" "mongodb/monitoring-integration.sh"

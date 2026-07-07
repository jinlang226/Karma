#!/usr/bin/env bash
# Generated from workflows/short/mongodb-auth-external-access-adversary.yaml

plan_stage "stage_01" "mongodb/custom-roles.sh"
plan_stage "stage_02" "mongodb/user-management.sh"
plan_stage "stage_03" "mongodb/external-access-horizons.sh"

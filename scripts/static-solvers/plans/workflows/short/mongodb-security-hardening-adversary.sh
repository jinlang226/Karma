#!/usr/bin/env bash
# Generated from workflows/short/mongodb-security-hardening-adversary.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/custom-roles.sh"
plan_stage "stage_03" "mongodb/user-management.sh"
plan_stage "stage_04" "mongodb/password-rotation.sh"

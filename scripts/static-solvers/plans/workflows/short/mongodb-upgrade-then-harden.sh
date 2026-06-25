#!/usr/bin/env bash
# Generated from workflows/short/mongodb-upgrade-then-harden.yaml

plan_stage "stage_01" "mongodb/version-upgrade.sh"
plan_stage "stage_02" "mongodb/custom-roles.sh"
plan_stage "stage_03" "mongodb/password-rotation.sh"

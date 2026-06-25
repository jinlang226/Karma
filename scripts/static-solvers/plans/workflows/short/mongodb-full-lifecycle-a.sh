#!/usr/bin/env bash
# Generated from workflows/short/mongodb-full-lifecycle-a.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/tls-setup.sh"
plan_stage "stage_04" "mongodb/custom-roles.sh"
plan_stage "stage_05" "mongodb/version-upgrade.sh"
plan_stage "stage_06" "mongodb/decommission.sh"

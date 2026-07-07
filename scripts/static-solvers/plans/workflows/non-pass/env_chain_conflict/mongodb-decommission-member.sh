#!/usr/bin/env bash
# Generated from workflows/non-pass/env_chain_conflict/mongodb-decommission-member.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/decommission.sh"

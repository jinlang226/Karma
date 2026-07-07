#!/usr/bin/env bash
# Generated from workflows/short/mongodb-decommission-member.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/decommission.sh"

#!/usr/bin/env bash
# Generated from workflows/short/mongodb-deploy-arbiters-roles-adversary.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/arbiters.sh"
plan_stage "stage_03" "mongodb/custom-roles.sh"
plan_stage "stage_04" "mongodb/user-management.sh"

#!/usr/bin/env bash
# Generated from workflows/short/mongodb-config-probe-customization-adversary.yaml

plan_stage "stage_01" "mongodb/mongod-config-update.sh"
plan_stage "stage_02" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_03" "mongodb/statefulset-customization.sh"

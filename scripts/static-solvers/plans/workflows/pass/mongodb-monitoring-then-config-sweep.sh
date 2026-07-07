#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-monitoring-then-config-sweep.yaml

plan_stage "stage_01" "mongodb/monitoring-integration.sh"
plan_stage "stage_02" "mongodb/mongod-config-update.sh"
plan_stage "stage_03" "mongodb/mongod-config-update.sh"

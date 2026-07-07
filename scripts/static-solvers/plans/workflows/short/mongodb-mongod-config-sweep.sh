#!/usr/bin/env bash
# Generated from workflows/short/mongodb-mongod-config-sweep.yaml

plan_stage "stage_01" "mongodb/mongod-config-update.sh"
plan_stage "stage_02" "mongodb/mongod-config-update.sh"
plan_stage "stage_03" "mongodb/mongod-config-update.sh"

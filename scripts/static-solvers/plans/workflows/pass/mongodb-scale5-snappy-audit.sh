#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-scale5-snappy-audit.yaml

plan_stage "stage_01" "mongodb/replica-scaling.sh"
plan_stage "stage_02" "mongodb/mongod-config-update.sh"
plan_stage "stage_03" "mongodb/readonly-audit.sh"

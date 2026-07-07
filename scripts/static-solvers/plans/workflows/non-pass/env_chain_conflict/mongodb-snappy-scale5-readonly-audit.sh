#!/usr/bin/env bash
# Generated from workflows/non-pass/env_chain_conflict/mongodb-snappy-scale5-readonly-audit.yaml

plan_stage "stage_01" "mongodb/mongod-config-update.sh"
plan_stage "stage_02" "mongodb/replica-scaling.sh"
plan_stage "stage_03" "mongodb/readonly-audit.sh"

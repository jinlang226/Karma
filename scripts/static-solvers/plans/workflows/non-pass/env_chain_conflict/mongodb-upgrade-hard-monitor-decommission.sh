#!/usr/bin/env bash
# Generated from workflows/non-pass/env_chain_conflict/mongodb-upgrade-hard-monitor-decommission.yaml

plan_stage "stage_01" "mongodb/version-upgrade-hard.sh"
plan_stage "stage_02" "mongodb/monitoring-integration.sh"
plan_stage "stage_03" "mongodb/decommission.sh"

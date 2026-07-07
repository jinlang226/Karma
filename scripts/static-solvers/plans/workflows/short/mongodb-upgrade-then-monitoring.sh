#!/usr/bin/env bash
# Generated from workflows/short/mongodb-upgrade-then-monitoring.yaml

plan_stage "stage_01" "mongodb/version-upgrade.sh"
plan_stage "stage_02" "mongodb/monitoring-integration.sh"

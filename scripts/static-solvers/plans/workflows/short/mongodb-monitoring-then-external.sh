#!/usr/bin/env bash
# Generated from workflows/short/mongodb-monitoring-then-external.yaml

plan_stage "stage_01" "mongodb/monitoring-integration.sh"
plan_stage "stage_02" "mongodb/external-access-horizons.sh"

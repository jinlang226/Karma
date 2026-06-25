#!/usr/bin/env bash
# Generated from workflows/short/mongodb-customization-then-monitoring.yaml

plan_stage "stage_01" "mongodb/statefulset-customization.sh"
plan_stage "stage_02" "mongodb/monitoring-integration.sh"

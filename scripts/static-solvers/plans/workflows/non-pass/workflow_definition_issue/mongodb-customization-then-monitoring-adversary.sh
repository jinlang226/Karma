#!/usr/bin/env bash
# Generated from workflows/non-pass/workflow_definition_issue/mongodb-customization-then-monitoring-adversary.yaml

plan_stage "stage_01" "mongodb/statefulset-customization.sh"
plan_stage "stage_02" "mongodb/monitoring-integration.sh"

#!/usr/bin/env bash
# Generated from workflows/short/mongodb-probe-then-customization.yaml

plan_stage "stage_01" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_02" "mongodb/statefulset-customization.sh"

#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-probe-then-customization-adversary.yaml

plan_stage "stage_01" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_02" "mongodb/statefulset-customization.sh"

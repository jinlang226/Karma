#!/usr/bin/env bash
# Generated from workflows/short/mongodb-readiness-probe-sweep.yaml

plan_stage "stage_01" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_02" "mongodb/readiness-probe-tuning.sh"

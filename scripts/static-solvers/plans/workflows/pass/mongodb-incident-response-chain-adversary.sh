#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-incident-response-chain-adversary.yaml

plan_stage "stage_01" "mongodb/health-check-recovery.sh"
plan_stage "stage_02" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_03" "mongodb/statefulset-customization.sh"

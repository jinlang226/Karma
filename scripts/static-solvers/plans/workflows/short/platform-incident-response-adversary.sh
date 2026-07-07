#!/usr/bin/env bash
# Generated from workflows/short/platform-incident-response-adversary.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/health-check-recovery.sh"
plan_stage "stage_03" "cockroachdb/deploy.sh"
plan_stage "stage_04" "cockroachdb/health-check-recovery.sh"

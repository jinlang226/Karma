#!/usr/bin/env bash
# Generated from workflows/short/platform-polyglot-persistence.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "cockroachdb/deploy.sh"
plan_stage "stage_04" "cockroachdb/initialize.sh"

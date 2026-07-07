#!/usr/bin/env bash
# Generated from workflows/pass/platform-data-bringup.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/initialize.sh"
plan_stage "stage_03" "mongodb/deploy.sh"
plan_stage "stage_04" "mongodb/initialize.sh"
plan_stage "stage_05" "elasticsearch/deploy-core-cluster.sh"

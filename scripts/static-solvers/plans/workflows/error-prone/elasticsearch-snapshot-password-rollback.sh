#!/usr/bin/env bash
# Generated from workflows/error-prone/elasticsearch-snapshot-password-rollback.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_03" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_04" "elasticsearch/rollback-rehearsal.sh"

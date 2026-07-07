#!/usr/bin/env bash
# Generated from workflows/pass/elasticsearch-certs-password-users-rollback.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_03" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_04" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_05" "elasticsearch/rollback-rehearsal.sh"

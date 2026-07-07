#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-password-rotation-sweep-ext.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_03" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_04" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_05" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_06" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_07" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_08" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_09" "elasticsearch/rotate-elastic-password.sh"

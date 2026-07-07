#!/usr/bin/env bash
# Generated from workflows/pass/platform-polyglot-persistence-ext.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/user-management.sh"
plan_stage "stage_04" "mongodb/tls-setup.sh"
plan_stage "stage_05" "cockroachdb/deploy.sh"
plan_stage "stage_06" "cockroachdb/initialize.sh"
plan_stage "stage_07" "cockroachdb/generate-cert.sh"
plan_stage "stage_08" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_09" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_10" "elasticsearch/scale-up-new-nodeset.sh"

#!/usr/bin/env bash
# Generated from workflows/error-prone/elasticsearch-scale-certs-password-snapshot-audit-rollback.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_04" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_05" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_06" "elasticsearch/readonly-audit.sh"
plan_stage "stage_07" "elasticsearch/rollback-rehearsal.sh"

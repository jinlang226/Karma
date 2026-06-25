#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-auth-voting-cleanup.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_03" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_04" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_05" "elasticsearch/transport-additional-ca-trust.sh"
plan_stage "stage_06" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_07" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_08" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_09" "elasticsearch/stack-monitoring-sidecars.sh"

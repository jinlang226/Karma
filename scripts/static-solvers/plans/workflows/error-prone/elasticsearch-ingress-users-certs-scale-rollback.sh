#!/usr/bin/env bash
# Generated from workflows/error-prone/elasticsearch-ingress-users-certs-scale-rollback.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_03" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_04" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_05" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_06" "elasticsearch/rollback-rehearsal.sh"

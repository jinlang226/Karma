#!/usr/bin/env bash
# Generated from workflows/error-prone/elasticsearch-ingress-scale6-password-rollback.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_03" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_04" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_05" "elasticsearch/rollback-rehearsal.sh"

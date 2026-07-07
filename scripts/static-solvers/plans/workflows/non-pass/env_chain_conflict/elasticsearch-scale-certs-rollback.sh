#!/usr/bin/env bash
# Generated from workflows/non-pass/env_chain_conflict/elasticsearch-scale-certs-rollback.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_04" "elasticsearch/rollback-rehearsal.sh"

#!/usr/bin/env bash
# Generated from workflows/error-prone/elasticsearch-certs-scale-change-plan.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_03" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_04" "elasticsearch/change-plan-only.sh"

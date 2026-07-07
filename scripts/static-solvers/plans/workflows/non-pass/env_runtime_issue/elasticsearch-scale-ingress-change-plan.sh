#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/elasticsearch-scale-ingress-change-plan.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_03" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_04" "elasticsearch/change-plan-only.sh"

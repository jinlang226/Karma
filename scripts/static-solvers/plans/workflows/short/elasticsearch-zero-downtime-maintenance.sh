#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-zero-downtime-maintenance.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_04" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_05" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_06" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_07" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_08" "elasticsearch/stack-monitoring-sidecars.sh"

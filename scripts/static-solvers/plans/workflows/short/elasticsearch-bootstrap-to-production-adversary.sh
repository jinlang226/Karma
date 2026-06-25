#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-bootstrap-to-production-adversary.yaml

plan_stage "stage_01" "elasticsearch/bootstrap-initial-master-nodes.sh"
plan_stage "stage_02" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_03" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_04" "elasticsearch/snapshot-repo-setup.sh"
plan_stage "stage_05" "elasticsearch/rotate-http-certs.sh"

#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-bootstrap-to-core.yaml

plan_stage "stage_01" "elasticsearch/bootstrap-initial-master-nodes.sh"
plan_stage "stage_02" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_03" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_04" "elasticsearch/rotate-http-certs.sh"

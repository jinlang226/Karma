#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-bootstrap-recovery.yaml

plan_stage "stage_01" "elasticsearch/bootstrap-initial-master-nodes.sh"
plan_stage "stage_02" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_03" "elasticsearch/internal-http-service-drift.sh"
plan_stage "stage_04" "elasticsearch/rotate-http-certs.sh"

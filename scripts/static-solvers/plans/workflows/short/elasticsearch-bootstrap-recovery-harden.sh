#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-bootstrap-recovery-harden.yaml

plan_stage "stage_01" "elasticsearch/bootstrap-initial-master-nodes.sh"
plan_stage "stage_02" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_03" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_04" "elasticsearch/file-realm-user-roles-merge.sh"
plan_stage "stage_05" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_06" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_07" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_08" "elasticsearch/stack-monitoring-sidecars.sh"

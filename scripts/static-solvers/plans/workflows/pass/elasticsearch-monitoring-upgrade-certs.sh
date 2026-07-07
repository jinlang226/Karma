#!/usr/bin/env bash
# Generated from workflows/pass/elasticsearch-monitoring-upgrade-certs.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_04" "elasticsearch/rotate-elastic-password.sh"

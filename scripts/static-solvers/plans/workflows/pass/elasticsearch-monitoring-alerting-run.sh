#!/usr/bin/env bash
# Generated from workflows/pass/elasticsearch-monitoring-alerting-run.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_03" "elasticsearch/rotate-elastic-password.sh"
plan_stage "stage_04" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_05" "elasticsearch/full-restart-upgrade-ha.sh"
plan_stage "stage_06" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_07" "elasticsearch/full-restart-upgrade-ha-hard.sh"
plan_stage "stage_08" "elasticsearch/stack-monitoring-sidecars.sh"
plan_stage "stage_09" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_10" "elasticsearch/stack-monitoring-sidecars.sh"

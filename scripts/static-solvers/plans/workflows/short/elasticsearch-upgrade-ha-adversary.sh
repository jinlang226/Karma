#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-upgrade-ha-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/full-restart-upgrade-ha.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"

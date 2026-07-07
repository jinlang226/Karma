#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-transport-ca-cert-rotation-adversary.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_03" "elasticsearch/rotate-elastic-password.sh"

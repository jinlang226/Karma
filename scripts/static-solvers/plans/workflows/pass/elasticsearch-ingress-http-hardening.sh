#!/usr/bin/env bash
# Generated from workflows/pass/elasticsearch-ingress-http-hardening.yaml

plan_stage "stage_01" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_02" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_03" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_04" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_05" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_06" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_07" "elasticsearch/secure-http-ingress.sh"
plan_stage "stage_08" "elasticsearch/stack-monitoring-sidecars.sh"

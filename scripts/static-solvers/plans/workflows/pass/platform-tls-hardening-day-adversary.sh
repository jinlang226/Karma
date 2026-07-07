#!/usr/bin/env bash
# Generated from workflows/pass/platform-tls-hardening-day-adversary.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/tls-setup.sh"
plan_stage "stage_03" "cockroachdb/generate-cert.sh"
plan_stage "stage_04" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_05" "elasticsearch/rotate-http-certs.sh"

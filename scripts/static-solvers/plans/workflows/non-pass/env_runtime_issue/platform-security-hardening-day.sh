#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/platform-security-hardening-day.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/generate-cert.sh"
plan_stage "stage_03" "cockroachdb/certificate-rotation.sh"
plan_stage "stage_04" "mongodb/deploy.sh"
plan_stage "stage_05" "mongodb/tls-setup.sh"
plan_stage "stage_06" "mongodb/certificate-rotation.sh"
plan_stage "stage_07" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_08" "elasticsearch/rotate-http-certs.sh"
plan_stage "stage_09" "elasticsearch/transport-additional-ca-trust.sh"
plan_stage "stage_10" "elasticsearch/secure-http-ingress.sh"

#!/usr/bin/env bash
# Generated from workflows/non-pass/env_runtime_issue/platform-full-lifecycle.yaml

plan_stage "stage_01" "cockroachdb/deploy.sh"
plan_stage "stage_02" "cockroachdb/generate-cert.sh"
plan_stage "stage_03" "mongodb/deploy.sh"
plan_stage "stage_04" "mongodb/tls-setup.sh"
plan_stage "stage_05" "mongodb/replica-scaling.sh"
plan_stage "stage_06" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_07" "elasticsearch/stack-monitoring-sidecars.sh"

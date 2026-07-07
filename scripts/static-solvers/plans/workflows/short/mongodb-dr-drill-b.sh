#!/usr/bin/env bash
# Generated from workflows/short/mongodb-dr-drill-b.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/replica-scaling.sh"
plan_stage "stage_04" "mongodb/tls-setup.sh"
plan_stage "stage_05" "mongodb/health-check-recovery.sh"
plan_stage "stage_06" "mongodb/readiness-probe-tuning.sh"
plan_stage "stage_07" "mongodb/password-rotation.sh"
plan_stage "stage_08" "mongodb/monitoring-integration.sh"
plan_stage "stage_09" "mongodb/mongod-config-update.sh"
plan_stage "stage_10" "mongodb/certificate-rotation.sh"

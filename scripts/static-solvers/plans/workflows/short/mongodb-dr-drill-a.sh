#!/usr/bin/env bash
# Generated from workflows/short/mongodb-dr-drill-a.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/tls-setup.sh"
plan_stage "stage_04" "mongodb/replica-scaling.sh"
plan_stage "stage_05" "mongodb/health-check-recovery.sh"
plan_stage "stage_06" "mongodb/statefulset-customization.sh"
plan_stage "stage_07" "mongodb/health-check-recovery.sh"
plan_stage "stage_08" "mongodb/mongod-config-update.sh"
plan_stage "stage_09" "mongodb/monitoring-integration.sh"
plan_stage "stage_10" "mongodb/certificate-rotation.sh"

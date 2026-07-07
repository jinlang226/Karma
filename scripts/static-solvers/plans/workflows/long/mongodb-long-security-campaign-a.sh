#!/usr/bin/env bash
# Generated from workflows/long/mongodb-long-security-campaign-a.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/tls-setup.sh"
plan_stage "stage_04" "mongodb/certificate-rotation.sh"
plan_stage "stage_05" "mongodb/custom-roles.sh"
plan_stage "stage_06" "mongodb/user-management.sh"
plan_stage "stage_07" "mongodb/password-rotation.sh"
plan_stage "stage_08" "mongodb/health-check-recovery.sh"
plan_stage "stage_09" "mongodb/external-access-horizons.sh"
plan_stage "stage_10" "mongodb/monitoring-integration.sh"
plan_stage "stage_11" "mongodb/certificate-rotation.sh"

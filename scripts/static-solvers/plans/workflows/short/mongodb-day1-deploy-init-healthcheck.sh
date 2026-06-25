#!/usr/bin/env bash
# Generated from workflows/short/mongodb-day1-deploy-init-healthcheck.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/initialize.sh"
plan_stage "stage_03" "mongodb/health-check-recovery.sh"

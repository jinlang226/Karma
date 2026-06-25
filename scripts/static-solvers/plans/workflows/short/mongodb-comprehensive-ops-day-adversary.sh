#!/usr/bin/env bash
# Generated from workflows/short/mongodb-comprehensive-ops-day-adversary.yaml

plan_stage "stage_01" "mongodb/initialize.sh"
plan_stage "stage_02" "mongodb/replica-scaling.sh"
plan_stage "stage_03" "mongodb/mongod-config-update.sh"
plan_stage "stage_04" "mongodb/tls-setup.sh"
plan_stage "stage_05" "mongodb/certificate-rotation.sh"

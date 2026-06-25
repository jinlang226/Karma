#!/usr/bin/env bash
# Generated from workflows/short/mongodb-full-lifecycle-c.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/tls-setup.sh"
plan_stage "stage_03" "mongodb/certificate-rotation.sh"
plan_stage "stage_04" "mongodb/replica-scaling.sh"
plan_stage "stage_05" "mongodb/version-upgrade-hard.sh"

#!/usr/bin/env bash
# Generated from workflows/short/mongodb-full-lifecycle-c.yaml

plan_stage "stage_01" "mongodb/version-upgrade-hard.sh"
plan_stage "stage_02" "mongodb/deploy.sh"
plan_stage "stage_03" "mongodb/tls-setup.sh"
plan_stage "stage_04" "mongodb/certificate-rotation.sh"
plan_stage "stage_05" "mongodb/replica-scaling.sh"

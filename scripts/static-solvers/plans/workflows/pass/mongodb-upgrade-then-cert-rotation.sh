#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-upgrade-then-cert-rotation.yaml

plan_stage "stage_01" "mongodb/version-upgrade.sh"
plan_stage "stage_02" "mongodb/certificate-rotation.sh"

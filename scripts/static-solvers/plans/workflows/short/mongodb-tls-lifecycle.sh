#!/usr/bin/env bash
# Generated from workflows/short/mongodb-tls-lifecycle.yaml

plan_stage "stage_01" "mongodb/tls-setup.sh"
plan_stage "stage_02" "mongodb/certificate-rotation.sh"

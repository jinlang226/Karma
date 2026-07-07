#!/usr/bin/env bash
# Generated from workflows/pass/mongodb-tls-lifecycle-adversary.yaml

plan_stage "stage_01" "mongodb/tls-setup.sh"
plan_stage "stage_02" "mongodb/certificate-rotation.sh"

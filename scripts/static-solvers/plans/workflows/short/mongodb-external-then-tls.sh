#!/usr/bin/env bash
# Generated from workflows/short/mongodb-external-then-tls.yaml

plan_stage "stage_01" "mongodb/external-access-horizons.sh"
plan_stage "stage_02" "mongodb/tls-setup.sh"

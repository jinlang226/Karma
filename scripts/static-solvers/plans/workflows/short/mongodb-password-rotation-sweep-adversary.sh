#!/usr/bin/env bash
# Generated from workflows/short/mongodb-password-rotation-sweep-adversary.yaml

plan_stage "stage_01" "mongodb/password-rotation.sh"
plan_stage "stage_02" "mongodb/custom-roles.sh"

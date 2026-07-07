#!/usr/bin/env bash
# Generated from workflows/short/mongodb-arbiters-then-scale.yaml

plan_stage "stage_01" "mongodb/arbiters.sh"
plan_stage "stage_02" "mongodb/replica-scaling.sh"

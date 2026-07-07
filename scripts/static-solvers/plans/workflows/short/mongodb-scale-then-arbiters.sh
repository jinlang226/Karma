#!/usr/bin/env bash
# Generated from workflows/short/mongodb-scale-then-arbiters.yaml

plan_stage "stage_01" "mongodb/replica-scaling.sh"
plan_stage "stage_02" "mongodb/arbiters.sh"

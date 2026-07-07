#!/usr/bin/env bash
# Generated from workflows/error-prone/ray-scale-only-rollback.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/scale_workers.sh"
plan_stage "stage_03" "ray/rollback-rehearsal.sh"

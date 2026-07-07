#!/usr/bin/env bash
# Generated from workflows/short/ray-scale-down-sweep.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/scale_workers.sh"
plan_stage "stage_03" "ray/scale_workers.sh"
plan_stage "stage_04" "ray/teardown_cluster.sh"

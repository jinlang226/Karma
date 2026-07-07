#!/usr/bin/env bash
# Generated from workflows/pass/ray-scale-upgrade-draft-rollback.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/scale_workers.sh"
plan_stage "stage_03" "ray/upgrade_version.sh"
plan_stage "stage_04" "ray/rollback-rehearsal.sh"

#!/usr/bin/env bash
# Generated from workflows/short/ray-deploy-dashboard-adversary.yaml

plan_stage "stage_01" "ray/deploy_cluster.sh"
plan_stage "stage_02" "ray/dashboard_exposure.sh"
plan_stage "stage_03" "ray/scale_workers.sh"

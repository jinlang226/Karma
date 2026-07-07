#!/usr/bin/env bash
# Generated from workflows/demo/workflow-demo-adversary.yaml

plan_stage "stage_01" "demo/configmap-update.sh"
plan_stage "stage_02" "demo/configmap-update.sh"
plan_stage "stage_03" "demo/configmap-update.sh"

#!/usr/bin/env bash
# Generated from workflows/error-prone/nginx-ingress-class-canary-rollback.yaml

plan_stage "stage_01" "nginx-ingress/class_only_upgrade.sh"
plan_stage "stage_02" "nginx-ingress/ingress_canary.sh"
plan_stage "stage_03" "nginx-ingress/rollback-rehearsal.sh"

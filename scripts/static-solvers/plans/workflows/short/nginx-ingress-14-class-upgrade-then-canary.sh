#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-14-class-upgrade-then-canary.yaml

plan_stage "stage_01" "nginx-ingress/class_only_upgrade.sh"
plan_stage "stage_02" "nginx-ingress/ingress_canary.sh"

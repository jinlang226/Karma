#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-17-otel-class-upgrade.yaml

plan_stage "stage_01" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_02" "nginx-ingress/class_only_upgrade.sh"

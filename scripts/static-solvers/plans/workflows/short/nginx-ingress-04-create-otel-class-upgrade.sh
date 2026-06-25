#!/usr/bin/env bash
# Generated from workflows/short/nginx-ingress-04-create-otel-class-upgrade.yaml

plan_stage "stage_01" "nginx-ingress/create_ingress.sh"
plan_stage "stage_02" "nginx-ingress/otel_log_format.sh"
plan_stage "stage_03" "nginx-ingress/class_only_upgrade.sh"

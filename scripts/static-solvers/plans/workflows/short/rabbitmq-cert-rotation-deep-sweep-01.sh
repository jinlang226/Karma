#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-cert-rotation-deep-sweep-01.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_user_permission.sh"
plan_stage "stage_03" "rabbitmq/manual_policy_sync.sh"
plan_stage "stage_04" "rabbitmq/manual_monitoring.sh"
plan_stage "stage_05" "rabbitmq/manual_monitoring.sh"

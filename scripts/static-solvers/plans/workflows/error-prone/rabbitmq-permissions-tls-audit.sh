#!/usr/bin/env bash
# Generated from workflows/error-prone/rabbitmq-permissions-tls-audit.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_user_permission.sh"
plan_stage "stage_03" "rabbitmq/manual_tls_rotation.sh"
plan_stage "stage_04" "rabbitmq/readonly-audit.sh"

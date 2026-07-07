#!/usr/bin/env bash
# Generated from workflows/pass/rabbitmq-permissions-monitoring-change-plan.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_user_permission.sh"
plan_stage "stage_03" "rabbitmq/manual_monitoring.sh"
plan_stage "stage_04" "rabbitmq/change-plan-only.sh"

#!/usr/bin/env bash
# Generated from workflows/error-prone/rabbitmq-permissions-change-plan-rollback.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_user_permission.sh"
plan_stage "stage_03" "rabbitmq/change-plan-only.sh"
plan_stage "stage_04" "rabbitmq/rollback-rehearsal.sh"

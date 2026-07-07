#!/usr/bin/env bash
# Generated from workflows/pass/rabbitmq-permissions-change-plan.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_user_permission.sh"
plan_stage "stage_03" "rabbitmq/change-plan-only.sh"

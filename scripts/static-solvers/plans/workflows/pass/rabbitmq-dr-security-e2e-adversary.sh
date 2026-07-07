#!/usr/bin/env bash
# Generated from workflows/pass/rabbitmq-dr-security-e2e-adversary.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_user_permission.sh"
plan_stage "stage_03" "rabbitmq/manual_policy_sync.sh"
plan_stage "stage_04" "rabbitmq/manual_monitoring.sh"

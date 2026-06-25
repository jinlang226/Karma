#!/usr/bin/env bash
# Generated from workflows/error-prone/rabbitmq-maximal-lifecycle-rollback-change-plan-audit.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_user_permission.sh"
plan_stage "stage_03" "rabbitmq/manual_policy_sync.sh"
plan_stage "stage_04" "rabbitmq/rollback-rehearsal.sh"
plan_stage "stage_05" "rabbitmq/manual_monitoring.sh"
plan_stage "stage_06" "rabbitmq/change-plan-only.sh"
plan_stage "stage_07" "rabbitmq/manual_policy_sync.sh"
plan_stage "stage_08" "rabbitmq/readonly-audit.sh"

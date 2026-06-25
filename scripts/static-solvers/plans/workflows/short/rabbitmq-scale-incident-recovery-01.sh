#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-scale-incident-recovery-01.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_policy_sync.sh"
plan_stage "stage_03" "rabbitmq/manual_user_permission.sh"
plan_stage "stage_04" "rabbitmq/manual_backup_restore.sh"
plan_stage "stage_05" "rabbitmq/manual_policy_sync.sh"
plan_stage "stage_06" "rabbitmq/manual_monitoring.sh"
plan_stage "stage_07" "rabbitmq/manual_skip_upgrade.sh"
plan_stage "stage_08" "rabbitmq/manual_monitoring.sh"

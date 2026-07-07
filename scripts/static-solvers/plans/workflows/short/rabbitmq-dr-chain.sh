#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-dr-chain.yaml

plan_stage "stage_01" "rabbitmq/failover.sh"
plan_stage "stage_02" "rabbitmq/classic_queue.sh"
plan_stage "stage_03" "rabbitmq/manual_policy_sync.sh"
plan_stage "stage_04" "rabbitmq/manual_backup_restore.sh"

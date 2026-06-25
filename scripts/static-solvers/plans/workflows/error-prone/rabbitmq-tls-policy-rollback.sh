#!/usr/bin/env bash
# Generated from workflows/error-prone/rabbitmq-tls-policy-rollback.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_tls_rotation.sh"
plan_stage "stage_03" "rabbitmq/manual_policy_sync.sh"
plan_stage "stage_04" "rabbitmq/rollback-rehearsal.sh"

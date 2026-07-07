#!/usr/bin/env bash
# Generated from workflows/pass/rabbitmq-policy-rollback.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_policy_sync.sh"
plan_stage "stage_03" "rabbitmq/rollback-rehearsal.sh"

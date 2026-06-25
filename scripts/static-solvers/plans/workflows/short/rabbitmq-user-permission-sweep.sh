#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-user-permission-sweep.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_user_permission.sh"
plan_stage "stage_03" "rabbitmq/manual_user_permission.sh"

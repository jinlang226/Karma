#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-failover-then-monitoring.yaml

plan_stage "stage_01" "rabbitmq/failover.sh"
plan_stage "stage_02" "rabbitmq/classic_queue.sh"
plan_stage "stage_03" "rabbitmq/manual_monitoring.sh"

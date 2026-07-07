#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-monitoring-resync-sweep-adversary.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_monitoring.sh"
plan_stage "stage_03" "rabbitmq/manual_monitoring.sh"

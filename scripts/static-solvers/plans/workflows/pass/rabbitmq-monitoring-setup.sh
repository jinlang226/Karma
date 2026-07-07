#!/usr/bin/env bash
# Generated from workflows/pass/rabbitmq-monitoring-setup.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_monitoring.sh"

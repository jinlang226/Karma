#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-skip-upgrade-3-12-to-4-1.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_skip_upgrade.sh"

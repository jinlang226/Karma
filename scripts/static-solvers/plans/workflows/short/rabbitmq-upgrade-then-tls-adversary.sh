#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-upgrade-then-tls-adversary.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_skip_upgrade.sh"
plan_stage "stage_03" "rabbitmq/manual_tls_rotation.sh"

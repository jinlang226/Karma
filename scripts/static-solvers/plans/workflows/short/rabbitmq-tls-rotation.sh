#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-tls-rotation.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_tls_rotation.sh"

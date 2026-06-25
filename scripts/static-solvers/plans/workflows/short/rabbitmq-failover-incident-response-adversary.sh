#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-failover-incident-response-adversary.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/failover.sh"

#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-classic-failover-adversary.yaml

plan_stage "stage_01" "rabbitmq/failover.sh"
plan_stage "stage_02" "rabbitmq/classic_queue.sh"

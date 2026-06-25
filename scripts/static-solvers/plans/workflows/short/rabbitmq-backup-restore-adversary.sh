#!/usr/bin/env bash
# Generated from workflows/short/rabbitmq-backup-restore-adversary.yaml

plan_stage "stage_01" "rabbitmq/classic_queue.sh"
plan_stage "stage_02" "rabbitmq/manual_backup_restore.sh"

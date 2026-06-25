#!/usr/bin/env bash
# Generated from workflows/rabbitmq-blue-green-migration-single.yaml

plan_stage "stage_1_migrate_source_to_target" "rabbitmq/blue_green_migration.sh"

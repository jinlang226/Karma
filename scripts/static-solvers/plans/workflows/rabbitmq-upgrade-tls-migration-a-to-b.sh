#!/usr/bin/env bash
# Generated from workflows/rabbitmq-upgrade-tls-migration-a-to-b.yaml

plan_stage "stage_1_upgrade_a_39_to_310" "rabbitmq/manual_skip_upgrade.sh"
plan_stage "stage_2_upgrade_b_39_to_311" "rabbitmq/manual_skip_upgrade.sh"
plan_stage "stage_3_rotate_tls_a" "rabbitmq/manual_tls_rotation.sh"
plan_stage "stage_4_rotate_tls_b" "rabbitmq/manual_tls_rotation.sh"
plan_stage "stage_5_migrate_a_to_b" "rabbitmq/blue_green_migration.sh"

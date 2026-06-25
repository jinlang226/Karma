#!/usr/bin/env bash
# Generated from workflows/short/elasticsearch-config-drift-recovery-adversary.yaml

plan_stage "stage_01" "elasticsearch/seed-hosts-repair.sh"
plan_stage "stage_02" "elasticsearch/transform-job-recovery.sh"
plan_stage "stage_03" "elasticsearch/rotate-http-certs.sh"

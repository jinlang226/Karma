#!/usr/bin/env bash
# Generated from workflows/error-prone/spark-sparkpi-audit-rollback.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/readonly-audit.sh"
plan_stage "stage_03" "spark/rollback-rehearsal.sh"

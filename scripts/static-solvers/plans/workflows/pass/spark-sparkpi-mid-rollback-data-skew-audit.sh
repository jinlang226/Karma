#!/usr/bin/env bash
# Generated from workflows/pass/spark-sparkpi-mid-rollback-data-skew-audit.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/rollback-rehearsal.sh"
plan_stage "stage_03" "spark/spark_data_skew.sh"
plan_stage "stage_04" "spark/readonly-audit.sh"

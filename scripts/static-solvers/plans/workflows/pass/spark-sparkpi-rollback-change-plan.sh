#!/usr/bin/env bash
# Generated from workflows/pass/spark-sparkpi-rollback-change-plan.yaml

plan_stage "stage_01" "spark/deploy_spark_pi.sh"
plan_stage "stage_02" "spark/rollback-rehearsal.sh"
plan_stage "stage_03" "spark/change-plan-only.sh"

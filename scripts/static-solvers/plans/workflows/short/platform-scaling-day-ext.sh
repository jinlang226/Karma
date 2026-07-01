#!/usr/bin/env bash
# Generated from workflows/short/platform-scaling-day-ext.yaml

plan_stage "stage_01" "mongodb/deploy.sh"
plan_stage "stage_02" "mongodb/replica-scaling.sh"
plan_stage "stage_03" "elasticsearch/deploy-core-cluster.sh"
plan_stage "stage_04" "elasticsearch/scale-up-new-nodeset.sh"
plan_stage "stage_05" "ray/deploy_cluster.sh"
plan_stage "stage_06" "ray/scale_workers.sh"
plan_stage "stage_07" "ray/worker_recovery.sh"
plan_stage "stage_08" "spark/deploy_spark_pi.sh"
plan_stage "stage_09" "spark/spark_streaming_autoscale.sh"

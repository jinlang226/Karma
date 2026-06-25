#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-rabbitmq}"
queue="${BENCH_PARAM_CANONICAL_QUEUE:-app-queue}"
stale_vhost="${BENCH_PARAM_STALE_VHOST:-stale}"
stale_user="${BENCH_PARAM_STALE_USER:-stale-user}"
stale_policy="${BENCH_PARAM_STALE_POLICY:-stale-policy}"
pod="${prefix}-0"

kubectl -n "$ns" exec "$pod" -- rabbitmqctl clear_policy -p /app "$stale_policy" || true
kubectl -n "$ns" exec "$pod" -- rabbitmqctl delete_user "$stale_user" || true
kubectl -n "$ns" exec "$pod" -- rabbitmqctl delete_vhost "$stale_vhost" || true
kubectl -n "$ns" exec "$pod" -- rabbitmqctl purge_queue -p /app "$queue"
printf 'removed RabbitMQ runtime drift\n' > submit.txt

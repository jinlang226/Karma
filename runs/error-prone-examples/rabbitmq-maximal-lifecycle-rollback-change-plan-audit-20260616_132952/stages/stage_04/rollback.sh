#!/bin/sh
# rollback.sh — Revert karma-rabbitmq-maximal-lifecycle-roll-0f5d4e03-cluster-a
#               back to RabbitMQ defaults.
#
# DO NOT EXECUTE against the live cluster until the change window is approved.
# This script is stored in the rollback-rehearsal ConfigMap for review only.
#
# Non-default configuration found at inspection time (2026-06-16):
#   Vhosts    : /app, /ops  (only / is default)
#   Users     : app-user [management], ops-user [management]
#   Permissions:
#     /app  -> admin (.*,.*,.*), app-user (.*,.*,.*)
#     /ops  -> admin (.*,.*,.*), ops-user (.*,.*,.*)
#   Policy    : ha-all on /app  (ha-mode=all, ha-sync-mode=automatic)
#   Queues    : app-queue (classic, durable) in /app
#               ops-queue (classic, durable) in /ops
#
# Rollback order:
#   1. Remove the HA policy from /app (explicit cleanup before vhost delete)
#   2. Delete queues in /app and /ops
#   3. Revoke non-default user permissions
#   4. Delete non-default vhosts (cascades: removes all queues, exchanges,
#      bindings, policies, and permissions within them)
#   5. Delete non-default users
#
# Note on 'admin' user: the default RabbitMQ user is 'guest'. The 'guest' user
# is absent from this cluster (intentionally removed for security in k8s).
# The 'admin' user serves as the operational administrator and is NOT removed
# here to avoid locking out cluster management. If restoring a true vanilla
# default is required, re-enable 'guest' (password: guest) and delete 'admin'
# separately after verifying an alternative admin path exists.

set -eu

NAMESPACE="karma-rabbitmq-maximal-lifecycle-roll-0f5d4e03-cluster-a"
POD="rabbitmq-0"

run() {
    kubectl -n "$NAMESPACE" exec "$POD" -- "$@"
}

echo "==> Step 1: Remove HA policy 'ha-all' from vhost /app"
run rabbitmqctl clear_policy -p /app ha-all

echo "==> Step 2: Delete queues in custom vhosts"
run rabbitmqctl delete_queue -p /app  app-queue
run rabbitmqctl delete_queue -p /ops  ops-queue

echo "==> Step 3: Revoke non-default user permissions from custom vhosts"
# Removing the users (step 5) also removes their permissions, but we do this
# explicitly so the intent is clear and the steps are independently auditable.
run rabbitmqctl clear_permissions -p /app app-user
run rabbitmqctl clear_permissions -p /ops ops-user
# admin's /app and /ops permissions are cleaned up implicitly by the vhost
# deletions below, but clear them first for explicitness.
run rabbitmqctl clear_permissions -p /app admin
run rabbitmqctl clear_permissions -p /ops admin

echo "==> Step 4: Delete non-default vhosts (cascades to any remaining queues,"
echo "            exchanges, bindings, policies, and permissions within them)"
run rabbitmqctl delete_vhost /app
run rabbitmqctl delete_vhost /ops

echo "==> Step 5: Delete non-default users"
run rabbitmqctl delete_user app-user
run rabbitmqctl delete_user ops-user

echo ""
echo "Rollback complete. Cluster should now have:"
echo "  Vhosts : / only"
echo "  Users  : admin [administrator] (guest absent — see note in script header)"
echo "  Perms  : admin has .* on /"
echo "  Policies: none"
echo "  Queues : none"

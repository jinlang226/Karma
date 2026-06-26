#!/bin/sh
#
# rollback.sh — revert the karma-rabbitmq cluster's non-default configuration
# back to RabbitMQ defaults.
#
# REVIEW-ONLY. Do NOT run this against the live cluster while the application
# still depends on the /app vhost, app-user, and app-queue. It is staged here
# for the next change window so the rollback can be reviewed before execution.
#
# Intended invocation (from a control host with kubectl access):
#   kubectl -n <namespace> exec rabbitmq-0 -- sh /path/to/rollback.sh
# or copy into the pod and run with rabbitmqctl on PATH.
#
# What it reverts (the only non-default settings found in the cluster):
#   - custom queue   : app-queue  (vhost /app)
#   - custom perms   : app-user   (vhost /app)
#   - custom user    : app-user
#   - custom vhost   : /app
#   - any policies on / and /app (defensive; none present at capture time)
#
# It deliberately leaves the bootstrap 'admin' user and the default '/' vhost
# in place, since those are the operational baseline (not application config).

set -eu

RABBITMQCTL="${RABBITMQCTL:-rabbitmqctl}"
APP_VHOST="/app"
APP_USER="app-user"
APP_QUEUE="app-queue"

echo "==> Removing custom queue '${APP_QUEUE}' on vhost '${APP_VHOST}'"
"${RABBITMQCTL}" delete_queue -p "${APP_VHOST}" "${APP_QUEUE}" || true

echo "==> Removing any policies on vhost '${APP_VHOST}'"
for pol in $("${RABBITMQCTL}" list_policies -p "${APP_VHOST}" --no-table-headers 2>/dev/null | awk '{print $2}'); do
  [ -n "${pol}" ] && "${RABBITMQCTL}" clear_policy -p "${APP_VHOST}" "${pol}" || true
done

echo "==> Removing any policies on vhost '/'"
for pol in $("${RABBITMQCTL}" list_policies -p "/" --no-table-headers 2>/dev/null | awk '{print $2}'); do
  [ -n "${pol}" ] && "${RABBITMQCTL}" clear_policy -p "/" "${pol}" || true
done

echo "==> Clearing custom permissions for user '${APP_USER}'"
"${RABBITMQCTL}" clear_permissions -p "${APP_VHOST}" "${APP_USER}" || true

echo "==> Deleting custom user '${APP_USER}'"
"${RABBITMQCTL}" delete_user "${APP_USER}" || true

echo "==> Deleting custom vhost '${APP_VHOST}'"
"${RABBITMQCTL}" delete_vhost "${APP_VHOST}" || true

echo "==> Rollback to RabbitMQ defaults complete."

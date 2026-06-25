#!/bin/sh
set -eu
python3 resources/rabbitmq-experiments/manual_user_permission/solver/solve.py
printf 'repaired RabbitMQ permissions\n' > submit.txt

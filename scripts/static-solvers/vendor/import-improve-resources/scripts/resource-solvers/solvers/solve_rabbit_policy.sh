#!/bin/sh
set -eu
python3 resources/rabbitmq-experiments/manual_policy_sync/solver/solve.py
printf 'synchronized RabbitMQ policy\n' > submit.txt

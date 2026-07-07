#!/bin/sh
set -eu
python3 resources/rabbitmq-experiments/manual_monitoring/solver/solve.py
printf 'repaired RabbitMQ monitoring\n' > submit.txt

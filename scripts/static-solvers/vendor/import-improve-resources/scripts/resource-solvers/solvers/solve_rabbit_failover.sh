#!/bin/sh
set -eu
python3 resources/rabbitmq-experiments/failover/solver/solve.py
printf 'repaired RabbitMQ failover state\n' > submit.txt

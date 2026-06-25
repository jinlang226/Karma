#!/bin/sh
set -eu
python3 resources/rabbitmq-experiments/manual_skip_upgrade/solver/solve.py
printf 'completed supported RabbitMQ upgrade path\n' > submit.txt

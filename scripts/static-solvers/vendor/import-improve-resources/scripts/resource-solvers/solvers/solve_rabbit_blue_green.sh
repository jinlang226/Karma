#!/bin/sh
set -eu
python3 resources/rabbitmq-experiments/blue_green_migration/solver/solve.py
printf 'completed RabbitMQ blue-green migration\n' > submit.txt

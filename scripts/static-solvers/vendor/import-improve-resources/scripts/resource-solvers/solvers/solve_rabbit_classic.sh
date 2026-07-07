#!/bin/sh
set -eu
python3 resources/rabbitmq-experiments/classic_queue/solver/solve.py
printf 'repaired RabbitMQ classic queue\n' > submit.txt

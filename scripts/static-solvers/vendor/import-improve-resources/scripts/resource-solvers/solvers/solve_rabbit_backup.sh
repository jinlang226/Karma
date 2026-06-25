#!/bin/sh
set -eu
python3 resources/rabbitmq-experiments/manual_backup_restore/solver/solve.py
printf 'restored RabbitMQ backup\n' > submit.txt

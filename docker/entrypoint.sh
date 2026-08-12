#!/bin/sh
set -e

# RabbitMQ state lives in /tmp so the container can run as any uid.
export RABBITMQ_ALLOW_INPUT_NON_SENSITIVE_DATA=1
export RABBITMQ_MNESIA_BASE=/tmp/rabbitmq-data
export RABBITMQ_LOG_BASE=/tmp/rabbitmq-data
export RABBITMQ_PID_FILE=/tmp/rabbitmq-data/rabbit.pid

mkdir -p /tmp/rabbitmq-data

# Start RabbitMQ in background
rabbitmq-server &

# Wait until RabbitMQ is ready
python3 - <<'EOF'
import socket, time
while True:
    try:
        s = socket.create_connection(('127.0.0.1', 5672), timeout=1)
        s.close()
        break
    except OSError:
        print("Waiting for RabbitMQ...")
        time.sleep(2)
EOF

echo "RabbitMQ is up and running."

# Dev convenience: when a CelebiChrono checkout is mounted (compose
# CELEBI_DIR), prefer it and the mounted Yuki source over installed packages.
if [ -n "$(ls -A /app/CelebiChrono 2>/dev/null)" ]; then
    export PYTHONPATH="/app/CelebiChrono:/app/Yuki:${PYTHONPATH:-}"
fi

exec yuki server start

#!/bin/bash

# Start RabbitMQ server in detached mode
rabbitmq-server -detached

# Wait for RabbitMQ to start (you can adjust the time or add more checks)
until rabbitmqctl status; do
  echo "Waiting for RabbitMQ to start..."
  sleep 5
done

echo "RabbitMQ is up and running."

# Start Yuki server
yuki server start

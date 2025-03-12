# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Update the package list and install dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    rabbitmq-server \
    && rm -rf /var/lib/apt/lists/*

# Enable RabbitMQ management plugin
RUN rabbitmq-plugins enable rabbitmq_management

# Set the working directory in the container
WORKDIR /app
COPY requirements.txt /app
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app
COPY Chern /app/Chern
COPY Yuki /app/Yuki

# Install the package using setup.py
WORKDIR /app/Chern
RUN python setup.py install

WORKDIR /app/Yuki
RUN python setup.py install

EXPOSE 3315

# Set the working directory
WORKDIR /app

# Copy the startup script to the container
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh


# Run the startup script
CMD ["/app/entrypoint.sh"]


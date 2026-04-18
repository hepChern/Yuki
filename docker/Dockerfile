# Production image for Yuki
# Builds from source and runs as non-root where possible
FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        rabbitmq-server \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY docker/requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt && \
    pip install --user build

# Install celebichrono dependency
RUN pip install --user celebichrono

# Copy source and build
COPY . /app/Yuki
WORKDIR /app/Yuki
RUN python -m build && \
    pip install --user dist/*.whl

WORKDIR /app
COPY docker/entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 3315

CMD ["/app/entrypoint.sh"]

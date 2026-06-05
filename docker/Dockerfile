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

# Python deps
RUN pip install --user --no-cache-dir click
RUN pip install --user --no-cache-dir colored
RUN pip install --user --no-cache-dir diff-match-patch
RUN pip install --user --no-cache-dir python-daemon
RUN pip install --user --no-cache-dir ipython
RUN pip install --user --no-cache-dir PyYAML==5.1
RUN pip install --user --no-cache-dir requests
RUN pip install --user --no-cache-dir reana-client
RUN pip install --user --no-cache-dir flask
RUN pip install --user --no-cache-dir celery
RUN pip install --user --no-cache-dir setuptools
RUN pip install --user --no-cache-dir pillow
# RUN pip install --user --no-cache-dir -r requirements.txt
RUN pip install --user build
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

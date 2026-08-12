# syntax=docker/dockerfile:1
# Multi-stage build for Yuki.
#   docker build --target dev  -t yuki:dev .
#   docker build --target prod -t yuki:<version> .   (prod is the default/final stage)

########## base ##########
FROM python:3.10-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        rabbitmq-server \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user; storage dir created here so named volumes
# inherit yuki ownership on first mount.
RUN useradd --create-home --uid 1000 --shell /bin/sh yuki && \
    mkdir -p /home/yuki/.Yuki && \
    chown -R yuki:yuki /home/yuki

RUN pip install --no-cache-dir build

COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 3315

########## dev ##########
# Editable install; docker-compose bind-mounts the repo over /app/Yuki
# (same path, so the editable install keeps working).
FROM base AS dev

WORKDIR /app/Yuki
COPY . /app/Yuki

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install celebichrono && \
    pip install -e .

USER yuki
WORKDIR /app
CMD ["/app/entrypoint.sh"]

########## prod (final stage = default) ##########
FROM base AS prod

WORKDIR /app/Yuki-src
COPY . /app/Yuki-src

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install celebichrono && \
    python -m build && \
    pip install dist/*.whl && \
    cd /app && rm -rf /app/Yuki-src

USER yuki
WORKDIR /app
CMD ["/app/entrypoint.sh"]

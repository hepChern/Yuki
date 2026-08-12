# Yuki

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/hepChern/Yuki)

Yuki is the Data Integration Thought Entity for the Chern Project. 

## Install:
+ Download the package
+ Run: python setup.py install

## Start:
+ yuki server start

## Stop:
+ Ctrl-C

## Run with Docker

The container is all-in-one: RabbitMQ (the Celery broker) starts inside it,
then `yuki server start` runs on port 3315 as a non-root user.

### Development (hot-reload)

```bash
# Builds the dev image and mounts this repo into the container —
# edits take effect without rebuilding
docker compose up

# Optional: develop against a local CelebiChrono checkout instead of PyPI
CELEBI_DIR=../CelebiChrono docker compose up
```

Storage persists in the `yuki-storage` volume (`/home/yuki/.Yuki` in the container).

### Building images

```bash
docker/scripts/build.sh dev            # yuki:dev
docker/scripts/build.sh prod           # yuki:<version> + yuki:latest (version from pyproject.toml)
docker/scripts/build.sh prod --tar     # also exports yuki-<version>.tar (for machines without registry access)
docker/scripts/build.sh prod --nightly # yuki-nightly:0.0.<date>-1 naming
```

### Running the production image

```bash
docker run -d -p 3315:3315 yuki:latest
```

Nightly images are also published to `ghcr.io` by CI.

